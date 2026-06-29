"""Target-stack adapters for generated Mint modules."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
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
        env = os.environ.copy()
        env["MINT_GENERATED_DIR"] = str(context.generated_dir)
        env["MINT_CONFORMANCE_DIR"] = str(context.conformance_dir)
        if required_paths:
            env["MINT_REQUIRED_SRC"] = os.pathsep.join(str(path) for path in required_paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["MINT_SKIP_PYTEST_VERSION_CHECK"] = "1"
        env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/mint-pycache")
        env.setdefault("PYTHON_BIN", sys.executable)
        return env


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
        result = subprocess.run(
            command,
            cwd=context.generated_dir,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            except (KeyError, TypeError, ValueError, OSError):
                continue
            candidates.append(
                {
                    "file": str(file_path),
                    "rel": rel,
                    "name": str(item.get("name", rel)),
                    "bodyStart": int(item["bodyStart"]),
                    "bodyEnd": int(item["bodyEnd"]),
                    "line": int(item.get("line", 0)),
                }
            )
        return sorted(candidates, key=lambda item: (item["rel"], item["bodyStart"], item["name"]))

    def _ts_mutate_and_test(
        self,
        context: Any,
        candidate: dict[str, Any],
        required_order: tuple[str, ...],
        config_path: Path,
    ) -> bool:
        path = Path(candidate["file"])
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(_ts_mutated_source(original, candidate), encoding="utf-8")
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
            path.write_text(original, encoding="utf-8")
            self.cleanup_runtime_caches(context.generated_dir, context.conformance_dir)


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


_TS_SIGNATURE_IN_TICKS_RE = re.compile(r"`([^`]*\([^`]*\)\s*:\s*[^`]*)`")
_TS_SIGNATURE_SPACED_RE = re.compile(
    r"(?:\bexport\s+)?(?:\bfunction\s+)?"
    r"\b(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*)\)\s*:\s*"
    r"(?P<return>[A-Za-z_$][A-Za-z0-9_$.\[\]<>|&?,{} ]*)"
)
_TS_EXPORT_FUNCTION_RE = re.compile(
    r"\bexport\s+(?:async\s+)?function\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*)\)\s*"
    r"(?::\s*(?P<return>[^{;\n]+))?",
    re.MULTILINE,
)
_TS_EXPORT_ARROW_RE = re.compile(
    r"\bexport\s+const\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?::[^=]+)?=\s*(?:async\s*)?"
    r"(?:\((?P<params>[^()]*)\)|(?P<single>[A-Za-z_$][\w$]*))\s*"
    r"(?::\s*(?P<return>[^=;\n]+))?\s*=>",
    re.MULTILINE,
)


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


def _typescript_source_signatures(src_dir: Path) -> dict[str, TypeScriptSignature]:
    signatures: dict[str, TypeScriptSignature] = {}
    if not src_dir.exists():
        return signatures
    for path in sorted(src_dir.rglob("*.ts")):
        if path.name.endswith(".d.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(src_dir).as_posix()
        for match in _TS_EXPORT_FUNCTION_RE.finditer(text):
            signature = _signature_from_match(match, rel)
            signatures.setdefault(signature.name, signature)
        for match in _TS_EXPORT_ARROW_RE.finditer(text):
            params = match.group("params")
            if params is None:
                params = match.group("single") or ""
            signature = TypeScriptSignature(
                name=match.group("name"),
                params=tuple(_parse_ts_params(params)),
                return_type=_clean_ts_type(match.group("return")),
                source=rel,
            )
            signatures.setdefault(signature.name, signature)
    return signatures


def _signature_from_match(match: re.Match[str], source: str) -> TypeScriptSignature:
    return TypeScriptSignature(
        name=match.group("name"),
        params=tuple(_parse_ts_params(match.group("params") or "")),
        return_type=_clean_ts_type(match.group("return")),
        source=source,
    )


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
    start = candidate["bodyStart"]
    end = candidate["bodyEnd"]
    throw = f'\nthrow new Error("mint mutation probe: {candidate["name"]}");\n'
    return source[:start] + throw + source[end:]


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
