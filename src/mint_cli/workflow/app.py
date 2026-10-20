"""Running rendered modules as an app.

A Mint "app" is nothing more than a module graph whose leaves have been rendered.
What used to be hand-written glue (a PYTHONPATH of five ``src`` dirs plus the
module-local ``.mint-deps``) is computed here from the same graph resolution the
conformance gate uses, so what you run is exactly what the gates tested:

- ``mint env <module>``  prints that environment (shell exports, JSON, or the bare path)
- ``mint run <module>``  runs the module's entrypoint (``python -m <entrypoint>``)
- ``mint exec <module> -- cmd …``  runs any command inside the environment

Only Python stacks compose this way for now; TypeScript composition is a known limit.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from ..errors import MintError
from ..stacks import PY_DEPS_DIRNAME
from ..state import load_metadata
from .context import ModuleContext, compute_context_hashes, load_context, resolve_required_order
from .planning import determine_render_plan

ENV_FORMATS = ("shell", "json", "path")


@dataclass(frozen=True)
class AppEnvironment:
    root: Path
    module: str
    # The module itself first, then its transitive requires in dependency order:
    # the same order the conformance scripts see in MINT_REQUIRED_SRC.
    modules: tuple[str, ...]
    src_paths: tuple[Path, ...]
    deps_paths: tuple[Path, ...]
    # Modules whose next render would not be a NOOP (spec or dependency moved).
    stale: tuple[str, ...]
    entrypoint: str | None

    @property
    def pythonpath(self) -> str:
        return os.pathsep.join(str(path) for path in (*self.src_paths, *self.deps_paths))


def resolve_app_environment(module: str, root: Path | None = None) -> AppEnvironment:
    """Resolve the generated sources an app needs, failing with a fix when any
    module of the graph is unrendered or not a Python stack."""
    top = load_context(module, root)
    order = [module, *resolve_required_order(top)]
    contexts = {name: (top if name == module else load_context(name, top.root)) for name in order}

    foreign = [
        f"{name} ({ctx.spec.stack})" for name, ctx in contexts.items() if ctx.stack_adapter.name != "python"
    ]
    if foreign:
        raise MintError(
            f"mint env/run/exec compose Python stacks only; not Python: {', '.join(foreign)}. "
            "TypeScript app composition is a known limit (docs/regenerative-system/known-limits.md)."
        )

    missing = [name for name, ctx in contexts.items() if not _is_rendered(ctx)]
    if missing:
        raise MintError(
            f"Not rendered: {', '.join(missing)} (no generated src and metadata). "
            f"Fix: mint render {module}"
        )

    stale = tuple(name for name, ctx in contexts.items() if _is_stale(ctx))
    src_paths = tuple(contexts[name].generated_dir / "src" for name in order)
    deps_paths = tuple(
        contexts[name].generated_dir / PY_DEPS_DIRNAME
        for name in order
        if (contexts[name].generated_dir / PY_DEPS_DIRNAME).is_dir()
    )
    return AppEnvironment(
        root=top.root,
        module=module,
        modules=tuple(order),
        src_paths=src_paths,
        deps_paths=deps_paths,
        stale=stale,
        entrypoint=top.spec.entrypoint,
    )


def _is_rendered(context: ModuleContext) -> bool:
    return (context.generated_dir / "src").is_dir() and load_metadata(context.generated_dir) is not None


def _is_stale(context: ModuleContext) -> bool:
    metadata = load_metadata(context.generated_dir)
    plan = determine_render_plan(context, metadata, None, None, False, compute_context_hashes(context))
    return not plan.noop


def environment_variables(app: AppEnvironment, base: dict[str, str] | None = None) -> dict[str, str]:
    """The process environment for running ``app``: the caller's environment plus
    the composed PYTHONPATH (generated sources win over whatever was there)."""
    env = dict(os.environ if base is None else base)
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = app.pythonpath + (os.pathsep + inherited if inherited else "")
    # Regenerated files can land in the same second with the same size as the
    # previous render; a stale .pyc would then shadow them (see known-limits.md).
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MINT_APP_MODULE"] = app.module
    env["MINT_APP_ROOT"] = str(app.root)
    return env


def _exported_variables(app: AppEnvironment) -> dict[str, str]:
    """Only the variables mint sets, for printing (never the caller's whole environ)."""
    full = environment_variables(app, base={})
    return {key: full[key] for key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "MINT_APP_MODULE", "MINT_APP_ROOT")}


def format_environment(app: AppEnvironment, fmt: str = "shell") -> str:
    if fmt not in ENV_FORMATS:
        raise MintError(f"Unknown env format '{fmt}'. Choose one of: {', '.join(ENV_FORMATS)}")
    if fmt == "path":
        return app.pythonpath + "\n"
    if fmt == "json":
        payload = {
            "module": app.module,
            "modules": list(app.modules),
            "entrypoint": app.entrypoint,
            "pythonpath": [str(path) for path in (*app.src_paths, *app.deps_paths)],
            "env": _exported_variables(app),
            "stale": list(app.stale),
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
    lines = [f"# mint env {app.module}: {', '.join(app.modules)}"]
    if app.stale:
        lines.append(f"# stale (re-render with `mint render {app.module}`): {', '.join(app.stale)}")
    lines.extend(f"export {key}={shlex.quote(value)}" for key, value in _exported_variables(app).items())
    return "\n".join(lines) + "\n"


def warn_if_stale(app: AppEnvironment) -> None:
    if app.stale:
        print(
            f"mint: warning: running stale generated code for {', '.join(app.stale)}; "
            f"re-render with `mint render {app.module}`",
            file=sys.stderr,
        )


def resolve_entrypoint(app: AppEnvironment, override: str | None = None) -> str:
    """The dotted module ``python -m`` runs: the CLI override, then the spec's
    ``entrypoint:`` key, then ``<module>`` with dashes as underscores when that
    package is runnable. Anything else is an error that names the candidates."""
    explicit = override or app.entrypoint
    if explicit:
        if not _runnable(app, explicit):
            raise MintError(
                f"Entrypoint '{explicit}' is not runnable under {_src_summary(app)}: "
                "expected <name>.py or <name>/__main__.py along that dotted path. "
                f"Runnable candidates: {_candidates_text(app)}"
            )
        return explicit
    default = app.module.replace("-", "_")
    if _runnable(app, default):
        return default
    raise MintError(
        f"Module '{app.module}' declares no entrypoint and '{default}' has no __main__.py. "
        f"Fix: add `entrypoint: <dotted.module>` to the spec frontmatter (it is not "
        f"hashed, so no re-render), or pass --entry. Runnable candidates: {_candidates_text(app)}"
    )


def _runnable(app: AppEnvironment, dotted: str) -> bool:
    parts = dotted.split(".")
    for src in app.src_paths:
        base = src.joinpath(*parts[:-1])
        if not base.is_dir():
            continue
        if (base / f"{parts[-1]}.py").is_file() or (base / parts[-1] / "__main__.py").is_file():
            return True
    return False


def _candidates(app: AppEnvironment) -> list[str]:
    found: list[str] = []
    src = app.src_paths[0]
    for path in sorted(src.rglob("*.py")):
        if any(part.startswith((".", "_")) and part != "__main__.py" for part in path.relative_to(src).parts):
            continue
        if path.name in ("__init__.py",):
            continue
        rel = path.relative_to(src).with_suffix("")
        dotted = ".".join(rel.parts[:-1] if path.name == "__main__.py" else rel.parts)
        if dotted and dotted not in found:
            found.append(dotted)
    return found


def _candidates_text(app: AppEnvironment) -> str:
    candidates = _candidates(app)
    return ", ".join(candidates) if candidates else "none found"


def _src_summary(app: AppEnvironment) -> str:
    return ", ".join(path.relative_to(app.root).as_posix() for path in app.src_paths)


def run_app(
    module: str,
    args: list[str],
    *,
    root: Path | None = None,
    entrypoint: str | None = None,
) -> int:
    app = resolve_app_environment(module, root)
    target = resolve_entrypoint(app, entrypoint)
    env = environment_variables(app)
    python = env.get("PYTHON_BIN") or sys.executable
    warn_if_stale(app)
    return _spawn([python, "-m", target, *args], env)


def exec_in_app(module: str, command: list[str], *, root: Path | None = None) -> int:
    if not command:
        raise MintError("mint exec needs a command: mint exec <module> -- <command> [args…]")
    app = resolve_app_environment(module, root)
    env = environment_variables(app)
    warn_if_stale(app)
    return _spawn(command, env)


def _spawn(command: list[str], env: dict[str, str]) -> int:
    """Run the child in the caller's working directory so relative file arguments
    mean what the user expects; return its exit code."""
    try:
        return subprocess.run(command, env=env, check=False).returncode
    except FileNotFoundError:
        raise MintError(f"Command not found: {command[0]}") from None
    except KeyboardInterrupt:
        return 130
