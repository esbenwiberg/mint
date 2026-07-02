from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .errors import MintError


GIT_IDENTITY = ["-c", "user.name=mint", "-c", "user.email=mint@example.invalid"]

# Isolation flags applied to every git invocation so a generated repo can never
# inherit user-level behaviour that would hang or corrupt an automated render:
#   commit.gpgsign=false -> never block on a GPG passphrase prompt
#   core.hooksPath=       -> never execute the user's global hooks in generated repos
GIT_ISOLATION = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath="]


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # Neutralise global/system config so a user's ~/.gitconfig (gpgsign, hooks,
    # aliases, commit templates) can never influence an automated render.
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def run_git(module_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(module_dir), *GIT_ISOLATION, *args],
            env=_git_env(),
            check=False,
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MintError(
            "git executable not found on PATH. "
            "Fix: install git and ensure it is on PATH, then rerun (see `mint doctor`)."
        ) from exc
    if check and result.returncode != 0:
        raise MintError(f"git {' '.join(args)} failed in {module_dir}: {result.stderr.strip()}")
    return result


def git_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"],
            check=False,
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def ensure_git_repo(module_dir: Path, module: str) -> None:
    module_dir.mkdir(parents=True, exist_ok=True)
    mintgen_dir = module_dir / ".mintgen"
    (mintgen_dir / "attempts").mkdir(parents=True, exist_ok=True)
    if not (module_dir / ".git").exists():
        run_git(module_dir, "init")
    if git_head(module_dir) is None:
        (mintgen_dir / ".gitkeep").write_text("", encoding="utf-8")
        commit_all(
            module_dir,
            f"[mint] initial module: {module}",
            f"Module: {module}\nUnit: none\nRender-Id: initial\nPrompt-Version: none\nModel: none\n",
        )


def git_head(module_dir: Path) -> str | None:
    result = run_git(module_dir, "rev-parse", "--verify", "HEAD", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_status(module_dir: Path) -> str:
    return run_git(module_dir, "status", "--porcelain").stdout


def commit_all(module_dir: Path, subject: str, body: str) -> str:
    run_git(module_dir, "add", "-A")
    if not git_status(module_dir).strip():
        head = git_head(module_dir)
        if head is None:
            raise MintError(f"No changes to commit and no HEAD in {module_dir}")
        return head
    run_git(module_dir, *GIT_IDENTITY, "commit", "-m", subject, "-m", body)
    head = git_head(module_dir)
    if head is None:
        raise MintError(f"Commit did not create a HEAD in {module_dir}")
    return head


def reset_hard(module_dir: Path, commit: str) -> None:
    run_git(module_dir, "reset", "--hard", commit)


def clean_untracked(module_dir: Path) -> None:
    """Remove untracked files and directories left by a failed/partial attempt.

    ``reset --hard`` only restores tracked files; untracked files an aborted
    attempt wrote survive and can make a later attempt's tests pass spuriously
    (and then get committed by ``commit_all``'s ``add -A``). Pair every checkpoint
    reset with a clean to guarantee a pristine tree.
    """
    run_git(module_dir, "clean", "-fd")
