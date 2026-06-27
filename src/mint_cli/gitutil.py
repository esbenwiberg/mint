from __future__ import annotations

from pathlib import Path
import subprocess

from .errors import MintError


GIT_IDENTITY = ["-c", "user.name=mint", "-c", "user.email=mint@example.invalid"]


def run_git(module_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(module_dir), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise MintError(f"git {' '.join(args)} failed in {module_dir}: {result.stderr.strip()}")
    return result


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

