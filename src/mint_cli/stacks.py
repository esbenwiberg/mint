"""Target-stack adapters for generated Mint modules."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from uuid import uuid4
from typing import Any, Protocol

from .errors import MintError


PYTEST_NO_TESTS = 5

# Subprocesses below run untrusted, model-generated code. Without a wall-clock
# ceiling an LLM-authored infinite loop hangs Mint forever (and the mutation probe
# multiplies each run by mutation_max_candidates). There is no timeout field in the
# config schema yet, so defaults live here and are overridable per-invocation via
# env vars. CROSS-AGENT ASSUMPTION: if a `limits.*TimeoutSeconds` config lands, wire
# it into `_timeout_seconds` without changing call sites.
_DEFAULT_TEST_TIMEOUT = 300.0
_DEFAULT_INSTALL_TIMEOUT = 600.0
_DEFAULT_PROBE_TIMEOUT = 60.0
# Exit code we synthesize for a timed-out / unlaunchable command.
_TIMEOUT_RETURNCODE = 124
_MISSING_BINARY_RETURNCODE = 127


def _timeout_seconds(env_var: str, default: float) -> float | None:
    """Resolve a subprocess timeout in seconds. `<=0` or unparsable disables it."""
    raw = os.environ.get(env_var)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else None


def _run_capture(
    command: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command capturing text output, turning timeouts and a missing binary
    into a failed CompletedProcess instead of an exception so callers get retryable
    feedback rather than a hard crash."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = _as_text(exc.stdout)
        err = _as_text(exc.stderr)
        label = " ".join(str(part) for part in command)
        err = (
            err
            + f"\nmint: command timed out after {timeout:g}s and was killed: {label}\n"
            "(fix: the generated code likely hangs — bound loops/IO, or raise "
            "MINT_TEST_TIMEOUT_SECONDS if the workload is legitimately long)\n"
        )
        return subprocess.CompletedProcess(command, _TIMEOUT_RETURNCODE, out, err)
    except OSError as exc:
        # e.g. the binary is absent (FileNotFoundError) — do not let this crash the
        # caller; surface it so the "X is required" branch stays reachable.
        return subprocess.CompletedProcess(
            command,
            _MISSING_BINARY_RETURNCODE,
            "",
            f"mint: could not execute {command[0]!r}: {exc}\n",
        )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _probe_binary(binary: str, *, cwd: Any = None) -> subprocess.CompletedProcess[str]:
    """`<binary> --version`, guarded so an absent binary yields a non-zero result
    (not a FileNotFoundError traceback that would kill `mint doctor`)."""
    if shutil.which(binary) is None:
        return subprocess.CompletedProcess(
            [binary, "--version"],
            _MISSING_BINARY_RETURNCODE,
            "",
            f"{binary} not found on PATH\n",
        )
    return _run_capture(
        [binary, "--version"],
        cwd=cwd,
        timeout=_timeout_seconds("MINT_PROBE_TIMEOUT_SECONDS", _DEFAULT_PROBE_TIMEOUT),
    )


@dataclass(frozen=True)
class StackHealth:
    messages: list[str]
    failures: list[str]


@dataclass(frozen=True)
class TypeScriptParam:
    name: str
    type: str | None = None


@dataclass(frozen=True)
class TypeScriptSignature:
    name: str
    params: tuple[TypeScriptParam, ...]
    return_type: str | None
    source: str


class StackAdapter(Protocol):
    """Boundary between Mint's render loop and target-stack tooling."""

    name: str
    stack_names: tuple[str, ...]
    code_fence_language: str
    supports_test_quality: bool
    unit_command_label: str
    conformance_command_label: str
    prepare_command_label: str

    def healthcheck(self, context: Any) -> StackHealth:
        ...

    def prepare(self, context: Any) -> subprocess.CompletedProcess[str]:
        ...

    def run_unit_tests(
        self,
        context: Any,
        *,
        required_order: tuple[str, ...],
        rendered_unit_ids: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        ...

    def run_conformance_tests(
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        ...

    def classify_test_result(
        self, phase: str, result: subprocess.CompletedProcess[str]
    ) -> str:
        ...

    def required_runtime_paths(self, context: Any, required_order: tuple[str, ...]) -> list[Path]:
        ...

    def required_payload_files(self, module_dir: Path) -> list[Path]:
        ...

    def required_context_files(self, module_dir: Path) -> list[dict[str, str]]:
        """Files (path/contents/language) a *dependent* module's render prompt embeds
        for this module — and the exact payload hashed into requiredModuleCodeHash.
        Keeping prompt context and cascade hash the same object means dependents
        re-render exactly when what they can see changes, and not otherwise."""
        ...

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        ...

    def cleanup_runtime_caches(self, *roots: Path) -> None:
        ...

    def test_quality_token_files(self, context: Any) -> list[Path]:
        """Files whose text feeds acceptance-traceability tokens."""
        ...

    def measure_coverage(
        self, context: Any, *, required_paths: list[Path]
    ) -> dict[str, Any]:
        """Measure generated-source coverage for the test-quality gate."""
        ...

    def run_mutation_probe(
        self,
        context: Any,
        *,
        required_paths: list[Path],
        baseline_already_passed: bool = False,
    ) -> dict[str, Any]:
        """Mutate generated source and confirm the tests catch it."""
        ...


class PythonStackAdapter:
    name = "python"
    stack_names = ("python-cli", "python-lib")
    code_fence_language = "python"
    supports_test_quality = True
    prepare_command_label = "configured prepare script"
    unit_command_label = "configured unit script"
    conformance_command_label = "configured conformance script"

    def healthcheck(self, context: Any) -> StackHealth:
        messages: list[str] = [f"Stack adapter: {self.name} ({context.spec.stack})"]
        failures: list[str] = []
        for label, script in [
            ("Prepare script", context.config.scripts.prepare),
            ("Unit script", context.config.scripts.unit),
            ("Conformance script", context.config.scripts.conformance),
        ]:
            path = context.root / script
            if not path.exists():
                failures.append(
                    f"{label} missing: {script} "
                    "(fix: create the file, or run `mint init --write` to restore default project files)"
                )
            elif not os.access(path, os.X_OK):
                failures.append(f"{label} is not executable: {script} (fix: chmod +x {script})")
            else:
                messages.append(f"{label}: {script}")
        return StackHealth(messages, failures)

    def prepare(self, context: Any) -> subprocess.CompletedProcess[str]:
        return _run_project_script(
            context,
            context.config.scripts.prepare,
            env=self.script_env(context, []),
        )

    def run_unit_tests(
        self,
        context: Any,
        *,
        required_order: tuple[str, ...],
        rendered_unit_ids: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        install = self._ensure_dependencies_installed(context, required_order)
        if install is not None:
            return install
        return _run_project_script(
            context,
            context.config.scripts.unit,
            env=self.script_env(context, self.required_runtime_paths(context, required_order)),
        )

    def run_conformance_tests(
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        install = self._ensure_dependencies_installed(context, required_order)
        if install is not None:
            return install
        return _run_project_script(
            context,
            context.config.scripts.conformance,
            env=self.script_env(context, self.required_runtime_paths(context, required_order)),
        )

    def _ensure_dependencies_installed(
        self, context: Any, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str] | None:
        """Install third-party deps from the generated pyproject.toml into a
        module-local ``.mint-deps`` dir, exposed to tests via the MINT_REQUIRED_SRC
        path channel (see ``script_env``). Local required Mint modules already ride
        PYTHONPATH, so they are filtered out rather than pip-resolved. Returns None
        on success or nothing-to-do; a failed CompletedProcess (retryable renderer
        feedback) when parsing or the install itself fails. Skips entirely when no
        third-party deps are declared, so offline projects never touch pip."""
        generated = context.generated_dir
        pyproject = generated / "pyproject.toml"
        if not pyproject.exists():
            return None
        try:
            declared = _python_project_dependencies(pyproject)
        except MintError as exc:
            return subprocess.CompletedProcess(["mint", "python-deps"], 1, "", f"{exc}\n")
        local = {_normalize_dist_name(context.module)}
        local.update(_normalize_dist_name(module) for module in required_order)
        requirements: list[str] = []
        for dep in declared:
            match = _REQUIREMENT_NAME_RE.match(dep)
            if match is None:
                return subprocess.CompletedProcess(
                    ["mint", "python-deps"],
                    1,
                    "",
                    f"Unparseable dependency {dep!r} in generated pyproject.toml "
                    "[project] dependencies; use standard requirement syntax.\n",
                )
            if _normalize_dist_name(match.group(1)) in local:
                continue
            requirements.append(dep.strip())
        if not requirements:
            return None
        target = generated / PY_DEPS_DIRNAME
        marker = target / _PY_DEPS_MARKER
        signature = json.dumps(sorted(requirements))
        if marker.exists():
            try:
                if marker.read_text(encoding="utf-8") == signature:
                    return None
            except OSError:
                pass
        # Deps changed (or first install): rebuild from scratch so removed
        # dependencies cannot linger and mask a missing declaration.
        shutil.rmtree(target, ignore_errors=True)
        _ensure_git_info_exclude(generated, (PY_DEPS_DIRNAME + "/",))
        override = os.environ.get("MINT_PY_INSTALL_COMMAND")
        if override:
            command = shlex.split(override)
        else:
            python_bin = os.environ.get("PYTHON_BIN") or sys.executable
            command = [
                python_bin,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--target",
                str(target),
                *requirements,
            ]
        env = os.environ.copy()
        env["MINT_PY_DEPS_TARGET"] = str(target)
        env["MINT_PY_DEPS_REQUIREMENTS"] = os.pathsep.join(requirements)
        result = _run_capture(
            command,
            cwd=generated,
            env=env,
            timeout=_timeout_seconds("MINT_INSTALL_TIMEOUT_SECONDS", _DEFAULT_INSTALL_TIMEOUT),
        )
        if result.returncode != 0:
            return result
        target.mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text(signature, encoding="utf-8")
        except OSError:
            pass
        return None

    def classify_test_result(
        self, phase: str, result: subprocess.CompletedProcess[str]
    ) -> str:
        if result.returncode == 0:
            return "passed"
        if result.returncode == PYTEST_NO_TESTS:
            return "no_tests"
        return f"{phase}_failed"

    def required_runtime_paths(self, context: Any, required_order: tuple[str, ...]) -> list[Path]:
        return [context.src_dir_for(module) for module in required_order]

    def required_payload_files(self, module_dir: Path) -> list[Path]:
        src = module_dir / "src"
        if not src.exists():
            return []
        return sorted(path for path in src.rglob("*.py") if path.is_file())

    def required_context_files(self, module_dir: Path) -> list[dict[str, str]]:
        # Dependents see the *public interface* (signatures + docstrings), not the
        # implementation. Internal-only changes upstream therefore neither alter
        # dependents' prompts (cassettes stay valid) nor their cascade hash.
        files: list[dict[str, str]] = []
        for path in self.required_payload_files(module_dir):
            rel = path.relative_to(module_dir).as_posix()
            name = path.name
            if name != "__init__.py" and name.startswith("_"):
                continue
            files.append(
                {
                    "path": rel,
                    "contents": python_interface_stub(path.read_text(encoding="utf-8")),
                    "language": "python",
                }
            )
        return files

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        hints = [
            "Write the implementation under src/ and generated unit tests under "
            "tests/ named test_*.py (pytest discovery); every functional unit must "
            "ship at least one unit test.",
            "Write the conformance test for the CURRENT unit only, with root "
            "'conformance', at the path FRn/... (for example FR1/test_fr1.py). Do not "
            "add a tests/ or module-name prefix, and do not create or modify earlier "
            "units' conformance tests.",
            "Do not write outside the generated module patch root or conformance "
            "patch root.",
            "Grow the public API incrementally for the current unit and prior "
            "rendered units; do not create placeholder stubs for future functional "
            "units.",
            "Declare third-party dependencies in the generated pyproject.toml "
            "[project] dependencies; Mint installs them before tests run.",
        ]
        if required_order:
            deps = ", ".join(required_order)
            hints.append(
                f"Required modules ({deps}) are shown as public interface stubs and "
                "are importable by package name at test time; do not pip-depend on "
                "them, and call them only through that public interface."
            )
        return hints

    def cleanup_runtime_caches(self, *roots: Path) -> None:
        for root in roots:
            _remove_python_caches(root)

    def test_quality_token_files(self, context: Any) -> list[Path]:
        from .test_quality import _test_files

        return _test_files(context)

    def measure_coverage(
        self, context: Any, *, required_paths: list[Path]
    ) -> dict[str, Any]:
        from .test_quality import _measure_coverage

        return _measure_coverage(context, required_src=required_paths)

    def run_mutation_probe(
        self,
        context: Any,
        *,
        required_paths: list[Path],
        baseline_already_passed: bool = False,
    ) -> dict[str, Any]:
        from .test_quality import _run_mutation_probe

        return _run_mutation_probe(
            context,
            required_src=required_paths,
            baseline_already_passed=baseline_already_passed,
        )

    def script_env(self, context: Any, required_paths: list[Path]) -> dict[str, str]:
        return script_env(context, required_paths)


class TypeScriptStackAdapter:
    name = "typescript"
    stack_names = ("typescript-lib", "typescript-node")
    code_fence_language = "typescript"
    supports_test_quality = True
    prepare_command_label = "node --version && npm --version"
    unit_command_label = "npm run typecheck && npm run test:unit"
    conformance_command_label = "npm run test:conformance"

    def healthcheck(self, context: Any) -> StackHealth:
        messages = [f"Stack adapter: {self.name} ({context.spec.stack})"]
        failures: list[str] = []
        for binary in ["node", "npm"]:
            result = _probe_binary(binary, cwd=context.root)
            if result.returncode == 0:
                messages.append(f"{binary}: {result.stdout.strip()}")
            else:
                failures.append(
                    f"{binary} is required for {context.spec.stack} "
                    "(fix: install Node.js with npm, then rerun mint doctor)"
                )

        package = context.generated_dir / "package.json"
        if package.exists():
            messages.append(f"Generated package: {package.relative_to(context.root).as_posix()}")
        return StackHealth(messages, failures)

    def prepare(self, context: Any) -> subprocess.CompletedProcess[str]:
        outputs: list[str] = []
        for binary in ["node", "npm"]:
            result = _probe_binary(binary, cwd=context.root)
            if result.returncode != 0:
                if not result.stderr.strip():
                    result = subprocess.CompletedProcess(
                        result.args,
                        result.returncode,
                        result.stdout,
                        f"{binary} is required for {context.spec.stack} "
                        "(fix: install Node.js with npm, then rerun).\n",
                    )
                return result
            outputs.append(f"{binary}: {result.stdout.strip()}")
        return subprocess.CompletedProcess(
            args=["mint", "typescript-prepare"],
            returncode=0,
            stdout="\n".join(outputs) + "\n",
            stderr="",
        )

    def run_unit_tests(
        self,
        context: Any,
        *,
        required_order: tuple[str, ...],
        rendered_unit_ids: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        self.wire_required_modules(context, required_order)
        self.prepare_typecheck_harness(context)
        signature_failures = self.validate_declared_signatures(
            context,
            rendered_unit_ids=rendered_unit_ids,
        )
        if signature_failures:
            return subprocess.CompletedProcess(
                args=["mint", "typescript-signature-check"],
                returncode=1,
                stdout="",
                stderr="\n".join(signature_failures) + "\n",
            )
        typecheck = self._run_npm_script(context, "typecheck", required_order=required_order)
        if typecheck.returncode != 0:
            return typecheck
        unit = self._run_npm_script(
            context, "test:unit", required_order=required_order, enforce_tests=True
        )
        return _combine_completed([typecheck, unit], args=["npm", "run", "typecheck+test:unit"])

    def run_conformance_tests(
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        self.wire_required_modules(context, required_order)
        self.prepare_typecheck_harness(context)
        package_name = _typescript_package_name(context.generated_dir) or context.module
        config_path = self.write_conformance_vitest_config(context, package_name)
        self.rewrite_conformance_src_imports(context, context.module)
        return self._run_npm_script(
            context,
            "test:conformance",
            "--config",
            str(config_path),
            str(context.conformance_dir),
            required_order=required_order,
            enforce_tests=True,
        )

    def classify_test_result(
        self, phase: str, result: subprocess.CompletedProcess[str]
    ) -> str:
        if result.returncode == 0:
            return "passed"
        output = (result.stdout + "\n" + result.stderr).lower()
        # Match only vitest's exact "no test files" phrasing; loose "no tests"
        # substrings misclassify genuine failures whose output happens to mention it.
        if "no test files found" in output:
            return "no_tests"
        return f"{phase}_failed"

    def required_runtime_paths(self, context: Any, required_order: tuple[str, ...]) -> list[Path]:
        return [context.generated_dir_for(module) for module in required_order]

    def required_payload_files(self, module_dir: Path) -> list[Path]:
        files: list[Path] = []
        for rel in ["package.json", "tsconfig.json"]:
            path = module_dir / rel
            if path.is_file():
                files.append(path)
        src = module_dir / "src"
        if src.exists():
            files.extend(
                sorted(
                    path
                    for pattern in ("*.ts", "*.tsx")
                    for path in src.rglob(pattern)
                    if path.is_file() and not path.name.endswith(".d.ts")
                )
            )
        return files

    def required_context_files(self, module_dir: Path) -> list[dict[str, str]]:
        # TypeScript dependents still see full source; interface-stub extraction for
        # TS is future work (the compiler-AST signature finder above is the seed).
        return [
            {
                "path": path.relative_to(module_dir).as_posix(),
                "contents": path.read_text(encoding="utf-8"),
                "language": self.code_fence_language,
            }
            for path in self.required_payload_files(module_dir)
        ]

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        hints = [
            "Generate a Node/npm TypeScript package under the module root only.",
            "Write package.json, tsconfig.json, src/**/*.ts, and tests/**/*.test.ts. "
            "Mint will normalize tsconfig moduleResolution to Bundler before tests run.",
            "package.json must include scripts: typecheck = `tsc --noEmit`, "
            "test:unit = `vitest run tests`, and test:conformance = `vitest run`.",
            "package.json devDependencies must include typescript, vitest, and "
            "@vitest/coverage-v8 so the test-quality coverage and mutation gates can run.",
            "Use Vitest for generated unit tests and conformance tests.",
            "Do not write vitest.conformance.config.ts; Mint owns that harness file.",
            "Conformance files must be written with root `conformance` under FRn/.",
            f"Conformance tests must import this module as `{context.module}`; "
            "Mint aliases that module name to src/index.ts for the conformance run.",
            "Do not import module code from conformance tests with relative ../src paths.",
            "Generated unit tests must assert only behavior stated by the current unit spec "
            "or acceptance bullets; do not invent unstated boundary cases.",
            "Treat backticked TypeScript signatures in the current unit spec as exact "
            "public API contracts.",
            "Grow the public API incrementally for the current unit and prior rendered units; "
            "do not create placeholder stubs for future functional units.",
            "Do not write outside the generated module patch root or conformance patch root.",
        ]
        if required_order:
            deps = ", ".join(required_order)
            hints.append(
                "Required modules are wired as package.json file dependencies before tests run: "
                f"{deps}. Import their public APIs by package name."
            )
        if context.spec.stack == "typescript-node":
            hints.append("For the Node stack, expose a CLI entry point through package.json bin.")
        return hints

    def cleanup_runtime_caches(self, *roots: Path) -> None:
        # Only known, top-level cache locations. A recursive rglob for "coverage" /
        # ".vite" / ".vitest" would happily rmtree a legitimate `src/coverage/` module
        # directory, destroying generated source and breaking the code hash.
        for root in roots:
            _remove_python_caches(root)
            if not root.exists():
                continue
            cache_paths = [
                root / ".vite",
                root / ".vitest",
                root / "coverage",
                root / ".nyc_output",
                root / "node_modules" / ".vite",
                root / "node_modules" / ".vitest",
                root / "node_modules" / ".cache",
            ]
            for path in cache_paths:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink(missing_ok=True)
            for path in root.glob("*.tsbuildinfo"):
                path.unlink(missing_ok=True)

    def wire_required_modules(self, context: Any, required_order: tuple[str, ...]) -> None:
        if not required_order:
            return
        package_path = context.generated_dir / "package.json"
        if not package_path.exists():
            return
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MintError(f"Generated package.json is invalid JSON: {exc}") from exc
        if not isinstance(package, dict):
            raise MintError("Generated package.json must contain a JSON object.")

        dependencies = package.get("dependencies")
        if not isinstance(dependencies, dict):
            dependencies = {}
            package["dependencies"] = dependencies

        changed = False
        for module in required_order:
            required_dir = context.generated_dir_for(module)
            package_name = _typescript_package_name(required_dir) or module
            rel = os.path.relpath(required_dir, context.generated_dir).replace(os.sep, "/")
            value = f"file:{rel}"
            if dependencies.get(package_name) != value:
                dependencies[package_name] = value
                changed = True

        if changed:
            package_path.write_text(
                json.dumps(package, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def prepare_typecheck_harness(self, context: Any) -> None:
        tsconfig_path = context.generated_dir / "tsconfig.json"
        if tsconfig_path.exists():
            # tsconfig.json is JSONC: comments and trailing commas are legal and LLMs
            # emit them routinely. Strip them before strict JSON parsing so ordinary
            # generated configs don't hard-abort the render.
            raw = tsconfig_path.read_text(encoding="utf-8")
            try:
                config = json.loads(_strip_jsonc(raw))
            except json.JSONDecodeError as exc:
                raise MintError(f"Generated tsconfig.json is invalid JSON: {exc}") from exc
            if not isinstance(config, dict):
                raise MintError("Generated tsconfig.json must contain a JSON object.")
        else:
            config = {}

        compiler_options = config.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            compiler_options = {}
            config["compilerOptions"] = compiler_options

        compiler_options.setdefault("target", "ES2022")
        compiler_options["moduleResolution"] = "Bundler"
        if str(compiler_options.get("module", "")).lower() in {"node16", "nodenext", ""}:
            compiler_options["module"] = "ESNext"
        compiler_options.setdefault("strict", True)
        config.setdefault("include", ["src/**/*.ts", "tests/**/*.ts"])

        tsconfig_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_conformance_vitest_config(self, context: Any, package_name: str) -> Path:
        config_path = context.generated_dir / "vitest.conformance.config.ts"
        aliases = {context.module: str(context.generated_dir / "src" / "index.ts")}
        if package_name != context.module:
            aliases[package_name] = str(context.generated_dir / "src" / "index.ts")
        alias_lines = "".join(
            f"      {json.dumps(name)}: {json.dumps(target)},\n"
            for name, target in sorted(aliases.items())
        )
        # Vitest roots this config at context.root, so the include glob must be derived
        # from the configured conformance dir (not hardcoded 'conformance/**').
        conf_rel = os.path.relpath(context.conformance_dir, context.root).replace(os.sep, "/")
        include_glob = f"{conf_rel}/**/*.test.ts"
        contents = (
            "import { defineConfig } from 'vitest/config';\n\n"
            "export default defineConfig({\n"
            f"  root: {json.dumps(str(context.root))},\n"
            "  test: {\n"
            f"    include: ['{include_glob}'],\n"
            "  },\n"
            "  resolve: {\n"
            "    alias: {\n"
            f"{alias_lines}"
            "    },\n"
            "  },\n"
            "});\n"
        )
        config_path.write_text(contents, encoding="utf-8")
        return config_path

    def rewrite_conformance_src_imports(self, context: Any, import_name: str) -> None:
        if not context.conformance_dir.exists():
            return
        pattern = re.compile(
            r"(?P<prefix>\bfrom\s+)(?P<quote>['\"])(?:\.\./)+src(?:/index)?(?:\.(?:ts|js))?(?P=quote)"
        )
        replacement = rf"\g<prefix>'{import_name}'"
        for path in sorted(context.conformance_dir.rglob("*.ts")):
            text = path.read_text(encoding="utf-8")
            rewritten = pattern.sub(replacement, text)
            if rewritten != text:
                path.write_text(rewritten, encoding="utf-8")

    def validate_declared_signatures(
        self,
        context: Any,
        *,
        rendered_unit_ids: tuple[str, ...] = (),
    ) -> list[str]:
        expected = _typescript_spec_signatures(context.spec, rendered_unit_ids=rendered_unit_ids)
        if not expected:
            return []
        actual = _typescript_source_signatures(context.generated_dir / "src")
        if actual is None:
            # The TS compiler could not parse the sources (tooling missing or a parse
            # error). We cannot verify signatures — skip rather than emit bogus
            # "no generated export named X" failures for code we simply didn't read.
            return []
        failures: list[str] = []
        for signature in expected:
            generated = actual.get(signature.name)
            if generated is None:
                failures.append(
                    "TypeScript signature mismatch: expected "
                    f"`{_format_ts_signature(signature)}` from spec, but no generated "
                    f"export named `{signature.name}` was found."
                )
                continue
            mismatch = _typescript_signature_mismatch(signature, generated)
            if mismatch:
                failures.append(
                    "TypeScript signature mismatch for "
                    f"`{signature.name}`: {mismatch}. "
                    f"Spec declared `{_format_ts_signature(signature)}` in: {signature.source}"
                )
        return failures

    def _run_npm_script(
        self,
        context: Any,
        script: str,
        *extra_args: str,
        required_order: tuple[str, ...],
        enforce_tests: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        package_path = context.generated_dir / "package.json"
        if not package_path.exists():
            return subprocess.CompletedProcess(
                args=["npm", "run", script],
                returncode=1,
                stdout="",
                stderr=(
                    "Generated TypeScript module is missing package.json. "
                    "The renderer must write package.json with typecheck, test:unit, "
                    "and test:conformance scripts.\n"
                ),
            )
        failures = self._package_script_failures(package_path)
        if failures:
            return subprocess.CompletedProcess(
                args=["npm", "run", script],
                returncode=1,
                stdout="",
                stderr="\n".join(failures) + "\n",
            )
        install = self._ensure_dependencies_installed(context)
        if install is not None:
            # Dependency install failed (bad/unresolvable deps, or npm error). Surface
            # it as retryable gate feedback instead of falling through to run scripts
            # against a broken/absent node_modules.
            return install
        env = self.script_env(context, self.required_runtime_paths(context, required_order))
        command = ["npm", "run", script]
        passthrough = list(extra_args)
        report_path: Path | None = None
        if enforce_tests:
            report_path = context.generated_dir / f".mint-vitest-{uuid4().hex}.json"
            passthrough.extend(
                ["--reporter=default", "--reporter=json", f"--outputFile.json={report_path}"]
            )
        if passthrough:
            command.append("--")
            command.extend(passthrough)
        result = _run_capture(
            command,
            cwd=context.generated_dir,
            env=env,
            timeout=_timeout_seconds("MINT_TEST_TIMEOUT_SECONDS", _DEFAULT_TEST_TIMEOUT),
        )
        if report_path is not None:
            result = self._enforce_nonzero_test_count(result, report_path, script)
        return result

    def _enforce_nonzero_test_count(
        self,
        result: subprocess.CompletedProcess[str],
        report_path: Path,
        script: str,
    ) -> subprocess.CompletedProcess[str]:
        """Fail a "passing" run that executed zero tests. The vitest JSON reporter is
        authoritative here; a substring check on human output is trivially gamed by
        `--passWithNoTests`. If no report was produced (e.g. tooling stubbed out) we
        leave the result untouched rather than guess."""
        count = _vitest_test_count(report_path)
        report_path.unlink(missing_ok=True)
        if result.returncode == 0 and count == 0:
            return subprocess.CompletedProcess(
                result.args,
                1,
                result.stdout,
                (result.stderr or "")
                + f"\nmint: {script} reported 0 executed tests. Zero tests cannot "
                "satisfy the gate — add real tests (do not use --passWithNoTests).\n",
            )
        return result

    def script_env(self, context: Any, required_paths: list[Path]) -> dict[str, str]:
        env = os.environ.copy()
        env["MINT_GENERATED_DIR"] = str(context.generated_dir)
        env["MINT_CONFORMANCE_DIR"] = str(context.conformance_dir)
        if required_paths:
            env["MINT_REQUIRED_MODULES"] = os.pathsep.join(str(path) for path in required_paths)
            env["MINT_REQUIRED_SRC"] = os.pathsep.join(str(path / "src") for path in required_paths)
        env["MINT_REQUIRED_MODULES_JSON"] = json.dumps(
            [
                {
                    "module": path.name,
                    "path": str(path),
                    "src": str(path / "src"),
                    "packageName": _typescript_package_name(path) or path.name,
                }
                for path in required_paths
            ],
            sort_keys=True,
        )
        return env

    def _package_script_failures(self, package_path: Path) -> list[str]:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [f"Generated package.json is invalid JSON: {exc}"]
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict):
            return ["Generated package.json must define scripts for TypeScript tests."]
        required = {
            "typecheck": "tsc --noEmit",
            "test:unit": "vitest run",
            "test:conformance": "vitest run",
        }
        failures: list[str] = []
        for name, expected in required.items():
            value = scripts.get(name)
            if not isinstance(value, str) or not value.strip():
                failures.append(f"Generated package.json is missing script {name!r}.")
                continue
            if expected not in " ".join(value.split()):
                failures.append(
                    f"Generated package.json script {name!r} must invoke `{expected}`."
                )
            if "--passwithnotests" in value.lower():
                failures.append(
                    f"Generated package.json script {name!r} must not use "
                    "`--passWithNoTests`: a run with zero tests must fail the gate."
                )
            metachars = sorted({ch for ch in value if ch in _SHELL_METACHARACTERS})
            if metachars:
                failures.append(
                    f"Generated package.json script {name!r} must be a single command; "
                    f"remove shell metacharacters ({' '.join(metachars)}). Chaining "
                    "(`&&`, `||`, `;`, pipes) can mask test failures."
                )
        return failures

    def _dependency_signature(self, package_path: Path) -> str:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""
        if not isinstance(package, dict):
            return ""
        relevant = {
            key: package.get(key)
            for key in (
                "dependencies",
                "devDependencies",
                "optionalDependencies",
                "peerDependencies",
            )
            if isinstance(package.get(key), dict)
        }
        return json.dumps(relevant, sort_keys=True)

    def _ensure_dependencies_installed(
        self, context: Any
    ) -> subprocess.CompletedProcess[str] | None:
        """Install npm dependencies into the generated module so file:-linked required
        modules and dev tooling resolve. Returns None on success; a failed
        CompletedProcess when the install itself failed (retryable feedback). Raises
        MintError only when install "succeeds" yet node_modules is still absent — an
        environment fault we must not paper over by falling back to global tooling.

        A full render rmtrees the generated dir (wiping node_modules), so install must
        happen lazily here, after package.json is written/wired — not in prepare()."""
        generated = context.generated_dir
        package_path = generated / "package.json"
        if not package_path.exists():
            # Missing package.json is reported by the caller with an actionable message.
            return None
        node_modules = generated / "node_modules"
        marker = generated / ".mint-npm-install.json"
        signature = self._dependency_signature(package_path)
        if node_modules.exists() and marker.exists():
            try:
                if marker.read_text(encoding="utf-8") == signature:
                    return None
            except OSError:
                pass
        override = os.environ.get("MINT_TS_INSTALL_COMMAND")
        command = (
            shlex.split(override)
            if override
            else ["npm", "install", "--no-audit", "--no-fund", "--no-progress"]
        )
        result = _run_capture(
            command,
            cwd=generated,
            env=self.script_env(context, []),
            timeout=_timeout_seconds("MINT_INSTALL_TIMEOUT_SECONDS", _DEFAULT_INSTALL_TIMEOUT),
        )
        if result.returncode != 0:
            return result
        if not node_modules.exists():
            raise MintError(
                "npm install reported success but node_modules is missing under "
                f"{generated}. Fix: check disk space and permissions, and that "
                "package.json dependencies resolve; Mint refuses to run tests against "
                "un-installed dependencies (which would silently use global tooling)."
            )
        try:
            marker.write_text(signature, encoding="utf-8")
        except OSError:
            pass
        return None

    # ---- test-quality (coverage + mutation) -------------------------------- #

    def test_quality_token_files(self, context: Any) -> list[Path]:
        files: list[Path] = []
        for root in [context.generated_dir / "tests", context.conformance_dir]:
            if root.exists():
                files.extend(sorted(root.rglob("*.ts")))
        return files

    def measure_coverage(
        self, context: Any, *, required_paths: list[Path]
    ) -> dict[str, Any]:
        threshold = context.config.test_quality.min_coverage_percent
        required_order = tuple(path.name for path in required_paths)
        self.wire_required_modules(context, required_order)
        self.prepare_typecheck_harness(context)
        src_dir = context.generated_dir / "src"
        reports_root = context.generated_dir / ".mint-coverage"
        shutil.rmtree(reports_root, ignore_errors=True)
        self.cleanup_runtime_caches(context.generated_dir)

        package_name = _typescript_package_name(context.generated_dir) or context.module
        config_path = self.write_conformance_vitest_config(context, package_name)
        self.rewrite_conformance_src_imports(context, context.module)

        # Coverage `include` globs are matched relative to each run's Vitest root: the
        # unit run roots at the generated dir, the conformance run roots at the repo
        # root (see write_conformance_vitest_config), so the src glob differs.
        conformance_src_glob = (
            os.path.relpath(src_dir, context.root).replace(os.sep, "/") + "/**"
        )
        targets: list[tuple[str, tuple[str, ...]]] = [
            ("unit", ("--coverage.include=src/**",)),
            (
                "conformance",
                (
                    "--config",
                    str(config_path),
                    str(context.conformance_dir),
                    f"--coverage.include={conformance_src_glob}",
                ),
            ),
        ]
        covered: dict[str, set[str]] = {}
        totals: dict[str, int] = {}
        try:
            for target, extra in targets:
                report_dir = reports_root / target
                script = "test:unit" if target == "unit" else "test:conformance"
                result = self._run_npm_script(
                    context,
                    script,
                    *extra,
                    *_TS_COVERAGE_FLAGS,
                    f"--coverage.reportsDirectory={report_dir}",
                    required_order=required_order,
                )
                report_path = report_dir / "coverage-final.json"
                if not report_path.exists():
                    text = (result.stdout + "\n" + result.stderr).lower()
                    if any(token in text for token in _TS_COVERAGE_MISSING_TOKENS):
                        return {
                            "status": "failed",
                            "reason": (
                                "coverage tooling missing: install the '@vitest/coverage-v8' "
                                "devDependency in the generated package before rendering"
                            ),
                            "threshold": threshold,
                            "percent": 0.0,
                            "coveredLines": 0,
                            "totalLines": 0,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    return {
                        "status": "failed",
                        "reason": f"coverage run failed for {target} tests",
                        "threshold": threshold,
                        "percent": 0.0,
                        "coveredLines": 0,
                        "totalLines": 0,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                if result.returncode != 0:
                    # A report can exist from a partial run even though tests failed;
                    # trusting it would let a failing suite report coverage.
                    return {
                        "status": "failed",
                        "reason": f"{target} tests failed during coverage run "
                        f"(exit {result.returncode})",
                        "threshold": threshold,
                        "percent": 0.0,
                        "coveredLines": 0,
                        "totalLines": 0,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                for rel, (hit, total) in _collect_istanbul_coverage(report_path, src_dir).items():
                    totals[rel] = total
                    covered.setdefault(rel, set()).update(hit)
        finally:
            shutil.rmtree(reports_root, ignore_errors=True)
            self.cleanup_runtime_caches(context.generated_dir)

        files = [
            {
                "path": rel,
                "coveredLines": len(covered.get(rel, set())),
                "totalLines": total,
                "percent": (len(covered.get(rel, set())) / total * 100.0) if total else 100.0,
            }
            for rel, total in sorted(totals.items())
        ]
        covered_total = sum(item["coveredLines"] for item in files)
        line_total = sum(item["totalLines"] for item in files)
        if not files or line_total == 0:
            # No measurable generated-source statements: an empty denominator must fail
            # rather than sail through as a vacuous 100%.
            return {
                "status": "failed",
                "reason": (
                    "coverage measured no generated source statements under src/ "
                    "(ensure tests import the generated module so its code is executed)"
                ),
                "threshold": threshold,
                "percent": 0.0,
                "coveredLines": 0,
                "totalLines": 0,
                "files": files,
            }
        percent = (covered_total / line_total * 100.0) if line_total else 100.0
        verdict: dict[str, Any] = {
            "status": "passed" if percent >= threshold else "failed",
            "threshold": threshold,
            "percent": percent,
            "coveredLines": covered_total,
            "totalLines": line_total,
            "files": files,
        }
        if verdict["status"] == "failed":
            verdict["reason"] = f"coverage {percent:.1f}% is below threshold {threshold}%"
        return verdict

    def run_mutation_probe(
        self,
        context: Any,
        *,
        required_paths: list[Path],
        baseline_already_passed: bool = False,
    ) -> dict[str, Any]:
        config = context.config.test_quality
        if not config.mutation_probe:
            return {"status": "skipped", "reason": "mutationProbe is false"}

        required_order = tuple(path.name for path in required_paths)
        self.wire_required_modules(context, required_order)
        self.prepare_typecheck_harness(context)
        package_name = _typescript_package_name(context.generated_dir) or context.module
        config_path = self.write_conformance_vitest_config(context, package_name)
        self.rewrite_conformance_src_imports(context, context.module)

        if not baseline_already_passed:
            baseline = self._ts_run_tests(context, required_order, config_path)
            if not baseline["ok"]:
                return {
                    "status": "failed",
                    "reason": "mutation baseline test run failed: " + baseline["reason"],
                    "candidateCount": 0,
                    "testedCandidates": 0,
                    "survivors": [],
                    "baseline": baseline["detail"],
                }

        candidates = self._ts_mutation_candidates(context)
        if candidates is None:
            return {
                "status": "failed",
                "reason": (
                    "mutation tooling missing: install the 'typescript' devDependency so the "
                    "TypeScript compiler can locate mutation candidates "
                    "(override discovery with MINT_TS_MUTATION_FINDER_COMMAND)"
                ),
                "candidateCount": 0,
                "testedCandidates": 0,
                "survivors": [],
            }

        tested = candidates[: config.mutation_max_candidates]
        survivors: list[dict[str, Any]] = []
        for candidate in tested:
            if not self._ts_mutate_and_test(context, candidate, required_order, config_path):
                survivors.append(
                    {"path": candidate["rel"], "name": candidate["name"], "line": candidate["line"]}
                )
                break

        if survivors:
            return {
                "status": "failed",
                "reason": "tests still passed after mutating generated implementation",
                "candidateCount": len(candidates),
                "testedCandidates": len(tested),
                "survivors": survivors,
            }
        return {
            "status": "passed" if tested else "skipped",
            "reason": "" if tested else "no public function candidates found",
            "candidateCount": len(candidates),
            "testedCandidates": len(tested),
            "survivors": [],
        }

    def _ts_run_tests(
        self, context: Any, required_order: tuple[str, ...], config_path: Path
    ) -> dict[str, Any]:
        unit = self._run_npm_script(context, "test:unit", required_order=required_order)
        conf = self._run_npm_script(
            context,
            "test:conformance",
            "--config",
            str(config_path),
            str(context.conformance_dir),
            required_order=required_order,
        )
        reasons: list[str] = []
        if unit.returncode != 0:
            reasons.append(f"unit tests exited {unit.returncode}")
        if conf.returncode != 0:
            reasons.append(f"conformance tests exited {conf.returncode}")
        return {
            "ok": not reasons,
            "reason": ", ".join(reasons),
            "detail": {
                "unit": {"exitCode": unit.returncode, "stdout": unit.stdout, "stderr": unit.stderr},
                "conformance": {
                    "exitCode": conf.returncode,
                    "stdout": conf.stdout,
                    "stderr": conf.stderr,
                },
            },
        }

    def _ts_mutation_candidates(self, context: Any) -> list[dict[str, Any]] | None:
        src_dir = context.generated_dir / "src"
        if not src_dir.exists():
            return []
        override = os.environ.get("MINT_TS_MUTATION_FINDER_COMMAND")
        command = shlex.split(override) if override else ["node", "-e", _TS_MUTATION_FINDER]
        env = os.environ.copy()
        env["MINT_TS_SRC"] = str(src_dir)
        result = _run_capture(
            command,
            cwd=context.generated_dir,
            env=env,
            timeout=_timeout_seconds("MINT_PROBE_TIMEOUT_SECONDS", _DEFAULT_PROBE_TIMEOUT),
        )
        if result.returncode != 0:
            return None
        try:
            raw = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, list):
            return None
        candidates: list[dict[str, Any]] = []
        for item in raw:
            try:
                file_path = Path(item["file"]).resolve()
                rel = file_path.relative_to(src_dir.resolve()).as_posix()
                record = {
                    "file": str(file_path),
                    "rel": rel,
                    "name": str(item.get("name", rel)),
                    "bodyStart": int(item["bodyStart"]),
                    "bodyEnd": int(item["bodyEnd"]),
                    "line": int(item.get("line", 0)),
                }
            except (KeyError, TypeError, ValueError, OSError):
                continue
            candidates.append(record)
        return sorted(candidates, key=lambda item: (item["rel"], item["bodyStart"], item["name"]))

    def _ts_mutate_and_test(
        self,
        context: Any,
        candidate: dict[str, Any],
        required_order: tuple[str, ...],
        config_path: Path,
    ) -> bool:
        path = Path(candidate["file"])
        # Read/write with newline="" so CRLF is preserved byte-for-byte: the finder's
        # offsets are measured over the raw file, and universal-newline translation
        # would shift every position after a CRLF and rewrite line endings on restore
        # (drifting the code hash).
        with open(path, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(_ts_mutated_source(original, candidate))
            self.cleanup_runtime_caches(context.generated_dir, context.conformance_dir)
            unit = self._run_npm_script(context, "test:unit", required_order=required_order)
            if unit.returncode != 0:
                return True
            conf = self._run_npm_script(
                context,
                "test:conformance",
                "--config",
                str(config_path),
                str(context.conformance_dir),
                required_order=required_order,
            )
            return conf.returncode != 0
        finally:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(original)
            self.cleanup_runtime_caches(context.generated_dir, context.conformance_dir)


# --------------------------------------------------------------------------- #
# Python public-interface stubs (required-module prompt context + cascade hash)
# --------------------------------------------------------------------------- #


def python_interface_stub(source: str) -> str:
    """Public-interface stub of a Python source file.

    Keeps the module docstring, imports, public constants, and public class /
    function signatures with their docstrings; every body becomes ``...``.
    Derived purely from the AST, so it is deterministic and — crucially — stable
    under internal-only edits (bodies, comments, private helpers). Falls back to
    the full source when the file does not parse, so context is never silently
    hidden behind a broken stub.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    body = list(tree.body)
    lines: list[str] = []
    docstring = ast.get_docstring(tree, clean=False)
    if docstring is not None:
        body = body[1:]
        lines.append(_stub_docstring(docstring))
    for node in body:
        stub = _stub_statement(node)
        if stub is not None:
            lines.append(stub)
    return ("\n\n".join(lines) + "\n") if lines else "...\n"


def _stub_docstring(docstring: str) -> str:
    if '"""' in docstring:
        return ast.unparse(ast.Expr(ast.Constant(docstring)))
    return f'"""{docstring}"""'


def _is_public_name(name: str) -> bool:
    # Single-underscore names are private; dunders (__all__, __init__, __eq__...)
    # are part of the public contract.
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _stub_statement(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return ast.unparse(node)
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return _stub_assignment(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if not _is_public_name(node.name):
            return None
        return ast.unparse(_stub_function(node))
    if isinstance(node, ast.ClassDef):
        if not _is_public_name(node.name):
            return None
        return ast.unparse(_stub_class(node))
    # Control flow (if TYPE_CHECKING:, try:), expression statements, etc. are
    # implementation detail — dependents cannot rely on them.
    return None


def _stub_assignment(node: ast.Assign | ast.AnnAssign) -> str | None:
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        targets = node.targets
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    if len(names) != len(targets) or not names or not all(_is_public_name(n) for n in names):
        return None
    clone = copy.deepcopy(node)
    if clone.value is not None and not _is_literal_expr(clone.value):
        clone.value = ast.Constant(value=Ellipsis)
    return ast.unparse(clone)


def _is_literal_expr(node: ast.expr) -> bool:
    allowed = (
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,
        ast.UnaryOp,
        ast.unaryop,
        ast.expr_context,
    )
    return all(isinstance(child, allowed) for child in ast.walk(node))


def _stub_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt:
    clone = copy.deepcopy(node)
    new_body: list[ast.stmt] = []
    docstring = ast.get_docstring(clone, clean=False)
    if docstring is not None:
        new_body.append(ast.Expr(ast.Constant(docstring)))
    new_body.append(ast.Expr(ast.Constant(value=Ellipsis)))
    clone.body = new_body
    return clone


def _stub_class(node: ast.ClassDef) -> ast.stmt:
    clone = copy.deepcopy(node)
    new_body: list[ast.stmt] = []
    docstring = ast.get_docstring(clone, clean=False)
    members = clone.body[1:] if docstring is not None else clone.body
    if docstring is not None:
        new_body.append(ast.Expr(ast.Constant(docstring)))
    for member in members:
        if isinstance(member, (ast.Assign, ast.AnnAssign)):
            stubbed = _stub_assignment(member)
            if stubbed is not None:
                new_body.append(ast.parse(stubbed).body[0])
        elif isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public_name(member.name):
                new_body.append(_stub_function(member))
        elif isinstance(member, ast.ClassDef):
            if _is_public_name(member.name):
                new_body.append(_stub_class(member))
    if not new_body:
        new_body.append(ast.Expr(ast.Constant(value=Ellipsis)))
    clone.body = new_body
    return clone


# --------------------------------------------------------------------------- #
# Python third-party dependency install (module-local, PYTHONPATH-exposed)
# --------------------------------------------------------------------------- #

PY_DEPS_DIRNAME = ".mint-deps"
_PY_DEPS_MARKER = ".mint-install.json"
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_project_dependencies(pyproject_path: Path) -> list[str]:
    import tomllib

    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MintError(f"Generated pyproject.toml is invalid TOML: {exc}") from exc
    project = data.get("project")
    deps = project.get("dependencies", []) if isinstance(project, dict) else []
    if not isinstance(deps, list):
        raise MintError("Generated pyproject.toml [project] dependencies must be a list.")
    return [str(dep) for dep in deps]


def _ensure_git_info_exclude(module_dir: Path, entries: tuple[str, ...]) -> None:
    """Ignore mint-owned runtime dirs in the generated repo via .git/info/exclude.

    Keeps them out of ``git add -A`` checkpoints and safe from the retry-path
    ``git clean -fd`` (which spares ignored files). The module's own .gitignore is
    model-owned, so mint must not rely on it."""
    git_dir = module_dir / ".git"
    if not git_dir.is_dir():
        return
    exclude = git_dir / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        missing = [entry for entry in entries if entry not in existing.splitlines()]
        if not missing:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + prefix + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError:
        # Non-fatal: worst case the deps dir lands in a checkpoint commit.
        pass


_PYTHON = PythonStackAdapter()
_TYPESCRIPT = TypeScriptStackAdapter()
_ADAPTERS: tuple[StackAdapter, ...] = (_PYTHON, _TYPESCRIPT)


def adapter_for_stack(stack: str) -> StackAdapter:
    key = (stack or "").strip().lower()
    for adapter in _ADAPTERS:
        if key in adapter.stack_names:
            return adapter
    known = ", ".join(sorted(name for adapter in _ADAPTERS for name in adapter.stack_names))
    raise MintError(f"Unsupported stack '{stack}'. Known stacks: {known}.")


def known_stacks() -> list[str]:
    return sorted(name for adapter in _ADAPTERS for name in adapter.stack_names)


def script_env(context: Any, required_paths: list[Path]) -> dict[str, str]:
    """Canonical environment for running Python project scripts and the coverage
    trace. Public so the render engine (workflow.py) can import it instead of keeping
    a drifted duplicate."""
    env = os.environ.copy()
    env["MINT_GENERATED_DIR"] = str(context.generated_dir)
    env["MINT_CONFORMANCE_DIR"] = str(context.conformance_dir)
    src_paths = [str(path) for path in required_paths]
    # Module-local third-party deps ride the same path channel as required module
    # sources, so project scripts, the conformance conftest, and the coverage trace
    # all see them without any script changes.
    deps_dir = context.generated_dir / PY_DEPS_DIRNAME
    if deps_dir.is_dir():
        src_paths.append(str(deps_dir))
    if src_paths:
        env["MINT_REQUIRED_SRC"] = os.pathsep.join(src_paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MINT_SKIP_PYTEST_VERSION_CHECK"] = "1"
    env.setdefault("PYTHON_BIN", sys.executable)
    return env


def _run_project_script(
    context: Any,
    script: str,
    *,
    env: dict[str, str],
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    path = context.root / script
    if not path.exists():
        raise MintError(
            f"Configured script not found: {script}. "
            "Fix: run mint doctor; create the file, or run `mint init --write` "
            "to restore default project files."
        )
    if not os.access(path, os.X_OK):
        raise MintError(f"Configured script is not executable: {script}. Fix: chmod +x {script}.")
    if timeout is None:
        timeout = _timeout_seconds("MINT_TEST_TIMEOUT_SECONDS", _DEFAULT_TEST_TIMEOUT)
    return _run_capture(
        [str(path), context.module],
        cwd=context.root,
        env=env,
        timeout=timeout,
    )


def _combine_completed(
    results: list[subprocess.CompletedProcess[str]], *, args: list[str]
) -> subprocess.CompletedProcess[str]:
    for result in results:
        if result.returncode != 0:
            return result
    return subprocess.CompletedProcess(
        args=args,
        returncode=0,
        stdout="\n".join(result.stdout.rstrip() for result in results if result.stdout.strip())
        + ("\n" if any(result.stdout.strip() for result in results) else ""),
        stderr="\n".join(result.stderr.rstrip() for result in results if result.stderr.strip())
        + ("\n" if any(result.stderr.strip() for result in results) else ""),
    )


def _typescript_package_name(module_dir: Path) -> str | None:
    package_path = module_dir / "package.json"
    if not package_path.exists():
        return None
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    name = package.get("name") if isinstance(package, dict) else None
    return str(name) if isinstance(name, str) and name.strip() else None


_TS_SIGNATURE_IN_TICKS_RE = re.compile(r"`([^`]*\([^`]*\)\s*:\s*[^`]*)`")
_TS_SIGNATURE_SPACED_RE = re.compile(
    r"(?:\bexport\s+)?(?:\bfunction\s+)?"
    r"\b(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*)\)\s*:\s*"
    r"(?P<return>[A-Za-z_$][A-Za-z0-9_$.\[\]<>|&?,{} ]*)"
)

# Shell metacharacters forbidden in the required package.json scripts. Present so a
# generated script like `vitest run || true` (which masks failures) is rejected.
_SHELL_METACHARACTERS = frozenset(";|&`$<>\n\r()")


def _typescript_spec_signatures(
    spec: Any, *, rendered_unit_ids: tuple[str, ...] = ()
) -> list[TypeScriptSignature]:
    signatures: list[TypeScriptSignature] = []
    seen: set[tuple[str, tuple[tuple[str, str | None], ...], str | None]] = set()
    allowed = set(rendered_unit_ids)
    texts: list[str] = []
    for unit in getattr(spec, "functional_units", []):
        if allowed and getattr(unit, "id", "") not in allowed:
            continue
        texts.extend(getattr(unit, "spec", []))

    for text in texts:
        for match in _TS_SIGNATURE_IN_TICKS_RE.finditer(text):
            signature = _parse_ts_signature(
                match.group(1),
                source=text,
            )
            if signature is None:
                continue
            key = (
                signature.name,
                tuple((param.name, param.type) for param in signature.params),
                signature.return_type,
            )
            if key not in seen:
                signatures.append(signature)
                seen.add(key)
    return signatures


def _typescript_source_signatures(src_dir: Path) -> dict[str, TypeScriptSignature] | None:
    """Extract exported function signatures from generated sources using the TS
    compiler AST (via node), the same technique the mutation finder uses. Regex
    extraction false-fails ordinary TypeScript (generics, function-typed params,
    object-literal return types, typed arrow consts). Returns None when the parse
    could not run at all so the caller skips validation instead of blocking.
    """
    if not src_dir.exists():
        return {}
    override = os.environ.get("MINT_TS_SIGNATURE_FINDER_COMMAND")
    command = shlex.split(override) if override else ["node", "-e", _TS_SIGNATURE_FINDER]
    env = os.environ.copy()
    env["MINT_TS_SRC"] = str(src_dir)
    result = _run_capture(
        command,
        cwd=src_dir,
        env=env,
        timeout=_timeout_seconds("MINT_PROBE_TIMEOUT_SECONDS", _DEFAULT_PROBE_TIMEOUT),
    )
    if result.returncode != 0:
        return None
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list):
        return None
    signatures: dict[str, TypeScriptSignature] = {}
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        params: list[TypeScriptParam] = []
        for param in item.get("params", []) or []:
            if not isinstance(param, dict):
                continue
            name = str(param.get("name", "")).strip()
            if name.startswith("..."):
                name = name[3:].strip()
            name = name.rstrip("?").strip()
            params.append(TypeScriptParam(name=name, type=_clean_ts_type(param.get("type"))))
        signature = TypeScriptSignature(
            name=str(item["name"]),
            params=tuple(params),
            return_type=_clean_ts_type(item.get("returnType")),
            source=str(item.get("source", "")),
        )
        signatures.setdefault(signature.name, signature)
    return signatures


def _parse_ts_signature(candidate: str, *, source: str) -> TypeScriptSignature | None:
    match = _TS_SIGNATURE_SPACED_RE.search(candidate.strip())
    if match is None:
        return None
    return TypeScriptSignature(
        name=match.group("name"),
        params=tuple(_parse_ts_params(match.group("params") or "")),
        return_type=_clean_ts_type(match.group("return")),
        source=source,
    )


def _parse_ts_params(params: str) -> list[TypeScriptParam]:
    result: list[TypeScriptParam] = []
    for raw in _split_ts_top_level(params):
        item = raw.strip()
        if not item:
            continue
        item = _strip_ts_default(item)
        name, param_type = _split_ts_param_type(item)
        name = name.strip()
        if name.startswith("..."):
            name = name[3:].strip()
        name = name.rstrip("?").strip()
        result.append(TypeScriptParam(name=name, type=_clean_ts_type(param_type)))
    return result


def _split_ts_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escape = False
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for index, char in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _strip_ts_default(param: str) -> str:
    depth = 0
    quote: str | None = None
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for index, char in enumerate(param):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        elif char == "=" and depth == 0:
            return param[:index]
    return param


def _split_ts_param_type(param: str) -> tuple[str, str | None]:
    depth = 0
    quote: str | None = None
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for index, char in enumerate(param):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        elif char == ":" and depth == 0:
            return param[:index], param[index + 1 :]
    return param, None


def _clean_ts_type(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().rstrip(" {=>;,.")
    return cleaned or None


def _normalize_ts_type(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments and trailing commas from JSONC (tsconfig.json)
    while preserving the contents of double-quoted strings."""
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    stripped = "".join(out)
    # Drop trailing commas before a closing } or ].
    return re.sub(r",(\s*[}\]])", r"\1", stripped)


def _typescript_signature_mismatch(
    expected: TypeScriptSignature, actual: TypeScriptSignature
) -> str | None:
    if len(expected.params) != len(actual.params):
        return f"expected {len(expected.params)} params but generated {len(actual.params)}"
    for index, (want, got) in enumerate(zip(expected.params, actual.params, strict=True), start=1):
        if _simple_identifier(want.name) and _simple_identifier(got.name) and want.name != got.name:
            return f"param {index} is `{got.name}` but spec declares `{want.name}`"
        if want.type and _normalize_ts_type(want.type) != _normalize_ts_type(got.type):
            return (
                f"param {index} type is `{got.type or '(missing)'}` "
                f"but spec declares `{want.type}`"
            )
    if expected.return_type and _normalize_ts_type(expected.return_type) != _normalize_ts_type(
        actual.return_type
    ):
        return (
            f"return type is `{actual.return_type or '(missing)'}` "
            f"but spec declares `{expected.return_type}`"
        )
    return None


def _simple_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_$][\w$]*", value))


def _format_ts_signature(signature: TypeScriptSignature) -> str:
    params = ", ".join(
        f"{param.name}: {param.type}" if param.type else param.name
        for param in signature.params
    )
    suffix = f": {signature.return_type}" if signature.return_type else ""
    return f"{signature.name}({params}){suffix}"


def _remove_python_caches(root: Path) -> None:
    if not root.exists():
        return
    for cache_dir in root.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in root.rglob(".pytest_cache"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in root.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)


_TS_COVERAGE_FLAGS = (
    "--coverage",
    "--coverage.provider=v8",
    "--coverage.reporter=json",
    "--coverage.all=true",
)

# Substrings vitest emits when the v8 coverage provider package is not installed.
_TS_COVERAGE_MISSING_TOKENS = (
    "@vitest/coverage",
    "coverage-v8",
    "coverage provider",
)


def _vitest_test_count(report_path: Path) -> int | None:
    """Total executed tests from a vitest JSON report, or None if unavailable.
    None means "could not determine" (report absent/unparsable) — callers must not
    treat that as zero."""
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("numTotalTests", "numTotalTestSuites"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    results = data.get("testResults")
    if isinstance(results, list):
        total = 0
        for suite in results:
            assertions = suite.get("assertionResults") if isinstance(suite, dict) else None
            if isinstance(assertions, list):
                total += len(assertions)
        return total
    return None


def _collect_istanbul_coverage(
    report_path: Path, src_dir: Path
) -> dict[str, tuple[set[str], int]]:
    """Map src-relative file -> (covered statement ids, total statements)."""
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    src_resolved = src_dir.resolve()
    result: dict[str, tuple[set[str], int]] = {}
    for abspath, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            rel = Path(abspath).resolve().relative_to(src_resolved).as_posix()
        except (ValueError, OSError):
            continue
        statements = entry.get("s")
        if not isinstance(statements, dict):
            continue
        covered = {key for key, count in statements.items() if isinstance(count, int) and count > 0}
        result[rel] = (covered, len(statements))
    return result


def _ts_mutated_source(source: str, candidate: dict[str, Any]) -> str:
    # The TypeScript compiler reports positions as UTF-16 code-unit offsets over the
    # raw file text. Python str indices are code points, so slicing `source` directly
    # misplaces the insertion whenever astral characters (e.g. an emoji in a comment)
    # precede the body. Slice over the UTF-16-LE encoding so offsets line up exactly.
    units = source.encode("utf-16-le")
    start = max(0, candidate["bodyStart"] * 2)
    end = min(len(units), candidate["bodyEnd"] * 2)
    if start > end:
        start = end
    name = str(candidate["name"]).replace("\\", "\\\\").replace('"', '\\"')
    throw = f'\nthrow new Error("mint mutation probe: {name}");\n'.encode("utf-16-le")
    return (units[:start] + throw + units[end:]).decode("utf-16-le")


# Emits one JSON record per mutatable exported function/method body span.
# bodyStart/bodyEnd are character offsets just inside the surrounding `{` / `}`.
_TS_MUTATION_FINDER = r"""
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const srcDir = process.env.MINT_TS_SRC;
const out = [];
function exported(node) {
  const mods = ts.canHaveModifiers(node) ? ts.getModifiers(node) : undefined;
  return !!(mods && mods.some(m => m.kind === ts.SyntaxKind.ExportKeyword));
}
function record(name, body, file, sf) {
  if (!body || !ts.isBlock(body)) return;
  const start = body.getStart(sf) + 1;
  const end = body.getEnd() - 1;
  if (end <= start) return;
  const line = sf.getLineAndCharacterOfPosition(body.getStart(sf)).line + 1;
  out.push({ file, name, bodyStart: start, bodyEnd: end, line });
}
function scan(file) {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  sf.forEachChild(node => {
    if (ts.isFunctionDeclaration(node) && node.name && exported(node) && !node.name.text.startsWith('_')) {
      record(node.name.text, node.body, file, sf);
    } else if (ts.isVariableStatement(node) && exported(node)) {
      node.declarationList.declarations.forEach(d => {
        const nm = d.name && d.name.getText(sf);
        if (nm && !nm.startsWith('_') && d.initializer && (ts.isArrowFunction(d.initializer) || ts.isFunctionExpression(d.initializer))) {
          record(nm, d.initializer.body, file, sf);
        }
      });
    } else if (ts.isClassDeclaration(node) && exported(node) && node.name) {
      node.members.forEach(m => {
        if (ts.isMethodDeclaration(m) && m.name) {
          const nm = m.name.getText(sf);
          if (!nm.startsWith('_')) record(node.name.text + '.' + nm, m.body, file, sf);
        }
      });
    }
  });
}
function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.tsx?$/.test(e.name) && !e.name.endsWith('.d.ts')) scan(p);
  }
}
walk(srcDir);
process.stdout.write(JSON.stringify(out));
"""


# Emits one JSON record per exported function/const-arrow signature using the TS
# compiler AST. Records: { name, params:[{name,type}], returnType, source }.
_TS_SIGNATURE_FINDER = r"""
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const srcDir = process.env.MINT_TS_SRC;
const out = [];
function exported(node) {
  const mods = ts.canHaveModifiers(node) ? ts.getModifiers(node) : undefined;
  return !!(mods && mods.some(m => m.kind === ts.SyntaxKind.ExportKeyword));
}
function typeText(node, sf) {
  return node ? node.getText(sf).replace(/\s+/g, ' ').trim() : null;
}
function collectParams(fn, sf) {
  return fn.parameters.map(p => ({
    name: p.name ? p.name.getText(sf) : '',
    type: p.type ? typeText(p.type, sf) : null,
  }));
}
function rel(file) {
  return path.relative(srcDir, file).split(path.sep).join('/');
}
function push(name, fn, sf, file) {
  if (!fn) return;
  out.push({
    name,
    params: collectParams(fn, sf),
    returnType: fn.type ? typeText(fn.type, sf) : null,
    source: rel(file),
  });
}
function scan(file) {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  sf.forEachChild(node => {
    if (ts.isFunctionDeclaration(node) && node.name && exported(node)) {
      push(node.name.text, node, sf, file);
    } else if (ts.isVariableStatement(node) && exported(node)) {
      node.declarationList.declarations.forEach(d => {
        const nm = d.name && d.name.getText(sf);
        if (nm && d.initializer && (ts.isArrowFunction(d.initializer) || ts.isFunctionExpression(d.initializer))) {
          push(nm, d.initializer, sf, file);
        }
      });
    }
  });
}
function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.tsx?$/.test(e.name) && !e.name.endsWith('.d.ts')) scan(p);
  }
}
walk(srcDir);
process.stdout.write(JSON.stringify(out));
"""
