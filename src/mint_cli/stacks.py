"""Target-stack adapters for generated Mint modules."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Protocol

from .errors import MintError


PYTEST_NO_TESTS = 5


@dataclass(frozen=True)
class StackHealth:
    messages: list[str]
    failures: list[str]


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
        self, context: Any, *, required_order: tuple[str, ...]
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

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        ...

    def cleanup_runtime_caches(self, *roots: Path) -> None:
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
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        return _run_project_script(
            context,
            context.config.scripts.unit,
            env=self.script_env(context, self.required_runtime_paths(context, required_order)),
        )

    def run_conformance_tests(
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        return _run_project_script(
            context,
            context.config.scripts.conformance,
            env=self.script_env(context, self.required_runtime_paths(context, required_order)),
        )

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

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        return []

    def cleanup_runtime_caches(self, *roots: Path) -> None:
        for root in roots:
            _remove_python_caches(root)

    def script_env(self, context: Any, required_paths: list[Path]) -> dict[str, str]:
        env = os.environ.copy()
        env["MINT_GENERATED_DIR"] = str(context.generated_dir)
        env["MINT_CONFORMANCE_DIR"] = str(context.conformance_dir)
        if required_paths:
            env["MINT_REQUIRED_SRC"] = os.pathsep.join(str(path) for path in required_paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/mint-pycache")
        env.setdefault("PYTHON_BIN", sys.executable)
        return env


class TypeScriptStackAdapter:
    name = "typescript"
    stack_names = ("typescript-lib", "typescript-node")
    code_fence_language = "typescript"
    supports_test_quality = False
    prepare_command_label = "node --version && npm --version"
    unit_command_label = "npm run typecheck && npm run test:unit"
    conformance_command_label = "npm run test:conformance"

    def healthcheck(self, context: Any) -> StackHealth:
        messages = [f"Stack adapter: {self.name} ({context.spec.stack})"]
        failures: list[str] = []
        for binary in ["node", "npm"]:
            result = subprocess.run(
                [binary, "--version"],
                cwd=context.root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
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
            result = subprocess.run(
                [binary, "--version"],
                cwd=context.root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                return result
            outputs.append(f"{binary}: {result.stdout.strip()}")
        return subprocess.CompletedProcess(
            args=["mint", "typescript-prepare"],
            returncode=0,
            stdout="\n".join(outputs) + "\n",
            stderr="",
        )

    def run_unit_tests(
        self, context: Any, *, required_order: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        self.wire_required_modules(context, required_order)
        self.prepare_typecheck_harness(context)
        typecheck = self._run_npm_script(context, "typecheck", required_order=required_order)
        if typecheck.returncode != 0:
            return typecheck
        unit = self._run_npm_script(context, "test:unit", required_order=required_order)
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
        )

    def classify_test_result(
        self, phase: str, result: subprocess.CompletedProcess[str]
    ) -> str:
        if result.returncode == 0:
            return "passed"
        output = (result.stdout + "\n" + result.stderr).lower()
        if "no test files" in output or "no tests" in output or "no test suite" in output:
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
                    if path.is_file() and not path.name.endswith(".d.ts.map")
                )
            )
        return files

    def prompt_hints(self, context: Any, required_order: tuple[str, ...]) -> list[str]:
        hints = [
            "Generate a Node/npm TypeScript package under the module root only.",
            "Write package.json, tsconfig.json, src/**/*.ts, and tests/**/*.test.ts. "
            "Mint will normalize tsconfig moduleResolution to Bundler before tests run.",
            "package.json must include scripts: typecheck = `tsc --noEmit`, "
            "test:unit = `vitest run tests`, and test:conformance = `vitest run`.",
            "Use Vitest for generated unit tests and conformance tests.",
            "Do not write vitest.conformance.config.ts; Mint owns that harness file.",
            "Conformance files must be written with root `conformance` under FRn/.",
            f"Conformance tests must import this module as `{context.module}`; "
            "Mint aliases that module name to src/index.ts for the conformance run.",
            "Do not import module code from conformance tests with relative ../src paths.",
            "Generated unit tests must assert only behavior stated by the current unit spec "
            "or acceptance bullets; do not invent unstated boundary cases.",
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
        for root in roots:
            _remove_python_caches(root)
            if not root.exists():
                continue
            for cache in [".vite", ".vitest", "coverage"]:
                for path in root.rglob(cache):
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    elif path.exists():
                        path.unlink(missing_ok=True)
            for path in root.rglob("*.tsbuildinfo"):
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
            try:
                config = json.loads(tsconfig_path.read_text(encoding="utf-8"))
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
        contents = (
            "import { defineConfig } from 'vitest/config';\n\n"
            "export default defineConfig({\n"
            f"  root: {json.dumps(str(context.root))},\n"
            "  test: {\n"
            "    include: ['conformance/**/*.test.ts'],\n"
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

    def _run_npm_script(
        self,
        context: Any,
        script: str,
        *extra_args: str,
        required_order: tuple[str, ...],
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
        env = self.script_env(context, self.required_runtime_paths(context, required_order))
        command = ["npm", "run", script]
        if extra_args:
            command.append("--")
            command.extend(extra_args)
        return subprocess.run(
            command,
            cwd=context.generated_dir,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

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
            elif expected not in " ".join(value.split()):
                failures.append(
                    f"Generated package.json script {name!r} must invoke `{expected}`."
                )
        return failures


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


def _run_project_script(
    context: Any,
    script: str,
    *,
    env: dict[str, str],
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
    return subprocess.run(
        [str(path), context.module],
        cwd=context.root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _remove_python_caches(root: Path) -> None:
    if not root.exists():
        return
    for cache_dir in root.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in root.rglob(".pytest_cache"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in root.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)
