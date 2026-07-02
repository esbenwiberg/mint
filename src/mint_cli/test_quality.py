"""Anti-weak-test quality gate for generated modules."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .stacks import (
    PYTEST_NO_TESTS,
    _run_capture,
    _run_project_script as _stacks_run_project_script,
    _timeout_seconds,
    adapter_for_stack,
    script_env as _stacks_script_env,
)

_DEFAULT_TEST_TIMEOUT = 300.0


@dataclass(frozen=True)
class MutationCandidate:
    path: Path
    relpath: str
    name: str
    lineno: int
    body_start: int
    body_end: int
    body_col: int


def evaluate_test_quality(
    context: Any,
    unit: Any,
    *,
    required_src: list[Path],
    baseline_already_passed: bool = False,
) -> dict[str, Any]:
    """Run traceability, coverage, and mutation checks for one rendered unit."""
    adapter = adapter_for_stack(context.spec.stack)
    config = context.config.test_quality
    if not config.enabled:
        return {
            "status": "skipped",
            "reason": "testQuality.enabled is false",
            "coverage": {"status": "skipped"},
            "traceability": [],
            "mutation": {"status": "skipped"},
        }
    if not adapter.supports_test_quality:
        reason = f"test-quality is not implemented for {context.spec.stack} yet"
        return {
            "status": "skipped",
            "reason": reason,
            "coverage": {"status": "skipped", "reason": reason},
            "traceability": [],
            "mutation": {"status": "skipped", "reason": reason},
        }

    traceability = _trace_acceptance_criteria(context, unit, adapter.test_quality_token_files(context))
    if _defer_coverage_and_mutation(context, unit):
        reason = "deferred until final functional unit for multi-unit module"
        # status stays "skipped" for backward-compatible verdict handling, but the
        # explicit deferred flag distinguishes "not yet due" from "genuinely skipped".
        coverage = {"status": "skipped", "reason": reason, "deferred": True}
        mutation = {"status": "skipped", "reason": reason, "deferred": True}
    else:
        coverage = adapter.measure_coverage(context, required_paths=required_src)
        mutation = adapter.run_mutation_probe(
            context,
            required_paths=required_src,
            baseline_already_passed=baseline_already_passed,
        )

    failures: list[str] = []
    if coverage["status"] == "failed":
        failures.append(coverage["reason"])
    missing = [item for item in traceability if item["status"] == "failed"]
    if missing:
        failures.append(
            "acceptance criteria missing test references: "
            + ", ".join(str(item["index"]) for item in missing)
        )
    if mutation["status"] == "failed":
        failures.append(mutation["reason"])

    return {
        "status": "failed" if failures else "passed",
        "failures": failures,
        "coverage": coverage,
        "traceability": traceability,
        "mutation": mutation,
    }


def _defer_coverage_and_mutation(context: Any, unit: Any) -> bool:
    units = list(getattr(context.spec, "functional_units", []))
    if len(units) <= 1:
        return False
    current_id = str(getattr(unit, "id", ""))
    final_id = str(getattr(units[-1], "id", ""))
    return current_id != final_id


def format_test_quality_verdict(verdict: dict[str, Any]) -> str:
    lines = [f"test-quality: {verdict.get('status')}"]
    coverage = verdict.get("coverage", {})
    if coverage:
        cov_status = coverage.get("status")
        if cov_status in {"skipped", "deferred"}:
            reason = coverage.get("reason", "")
            label = "deferred" if coverage.get("deferred") else cov_status
            lines.append(f"coverage: {label}" + (f" ({reason})" if reason else ""))
        else:
            lines.append(
                "coverage: "
                f"{coverage.get('percent', 0):.1f}% "
                f"({coverage.get('coveredLines', 0)}/{coverage.get('totalLines', 0)} lines, "
                f"threshold {coverage.get('threshold', 0)}%)"
            )
    traceability = verdict.get("traceability", [])
    if traceability:
        passed = sum(1 for item in traceability if item.get("status") == "passed")
        lines.append(f"traceability: {passed}/{len(traceability)} criteria referenced")
    mutation = verdict.get("mutation", {})
    if mutation:
        lines.append(
            "mutation: "
            f"{mutation.get('status')} "
            f"({mutation.get('testedCandidates', 0)}/{mutation.get('candidateCount', 0)} candidates)"
        )
    for failure in verdict.get("failures", []):
        lines.append(f"failure: {failure}")
    return "\n".join(lines) + "\n"


def _trace_acceptance_criteria(
    context: Any, unit: Any, token_files: list[Path]
) -> list[dict[str, Any]]:
    test_tokens = _test_tokens(token_files)
    verdicts: list[dict[str, Any]] = []
    for index, criterion in enumerate(unit.acceptance, start=1):
        tokens = sorted(_criterion_tokens(criterion))
        matched = [token for token in tokens if token in test_tokens]
        if not tokens:
            # No distinctive identifier-like tokens to trace (e.g. an all-stopword
            # criterion). We cannot prove or disprove a reference — skip, don't fail.
            status = "skipped"
            required = 0
        else:
            required = min(2, len(tokens))
            status = "passed" if len(matched) >= required else "failed"
        verdicts.append(
            {
                "index": index,
                "criterion": criterion,
                "tokens": tokens,
                "matchedTokens": matched,
                "requiredMatches": required,
                "status": status,
            }
        )
    return verdicts


def _test_tokens(token_files: list[Path]) -> set[str]:
    # Tokenize test *code* only: comments are stripped first so a criterion pasted
    # into a comment cannot satisfy traceability without a real test reference.
    tokens: set[str] = set()
    for path in token_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        tokens.update(_tokens(_strip_source_comments(text, path.suffix.lower())))
    return tokens


def _strip_source_comments(text: str, suffix: str) -> str:
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}:
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
        text = re.sub(r"(?m)//.*$", " ", text)
    else:  # python and friends
        text = re.sub(r"(?m)#.*$", " ", text)
    return text


def _test_files(context: Any) -> list[Path]:
    files: list[Path] = []
    for root in [context.generated_dir / "tests", context.conformance_dir]:
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _criterion_tokens(text: str) -> set[str]:
    return {
        token
        for token in _tokens(text)
        if token not in _TRACE_STOPWORDS and (len(token) > 2 or token.isdigit())
    }


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text)}


_TRACE_STOPWORDS = {
    "after",
    "and",
    "are",
    "before",
    "calling",
    "comes",
    "expected",
    "has",
    "one",
    "output",
    "returns",
    "shows",
    "that",
    "the",
    "then",
    "this",
    "via",
    "with",
}


def _measure_coverage(context: Any, *, required_src: list[Path]) -> dict[str, Any]:
    result = _run_trace_process(context, required_src=required_src)
    threshold = context.config.test_quality.min_coverage_percent
    if result["exitCode"] != 0:
        if result["exitCode"] == PYTEST_NO_TESTS:
            reason = (
                "coverage trace collected no tests (pytest exit 5): the unit or "
                "conformance suite has no tests to run"
            )
        else:
            reason = "coverage trace run failed"
        return {
            "status": "failed",
            "reason": reason,
            "threshold": threshold,
            "percent": 0.0,
            "coveredLines": 0,
            "totalLines": 0,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    covered = int(result["coveredLines"])
    total = int(result["totalLines"])
    if total == 0:
        return {
            "status": "failed",
            "reason": (
                "coverage measured no executable generated-source lines "
                "(ensure tests import the generated module so its code runs)"
            ),
            "threshold": threshold,
            "percent": 0.0,
            "coveredLines": 0,
            "totalLines": 0,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
    percent = (covered / total * 100.0) if total else 100.0
    status = "passed" if percent >= threshold else "failed"
    verdict = {
        "status": status,
        "threshold": threshold,
        "percent": percent,
        "coveredLines": covered,
        "totalLines": total,
        "files": result["files"],
    }
    if status == "failed":
        verdict["reason"] = f"coverage {percent:.1f}% is below threshold {threshold}%"
    return verdict


def _run_trace_process(context: Any, *, required_src: list[Path]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    timeout = _timeout_seconds("MINT_TEST_TIMEOUT_SECONDS", _DEFAULT_TEST_TIMEOUT)
    for target in ["unit", "conformance"]:
        env = _script_env(context, required_src)
        env["MINT_TRACE_TARGET"] = target
        # Run under the same interpreter the project scripts honour (PYTHON_BIN),
        # so a project-venv user's deps are visible to the trace too.
        interpreter = env.get("PYTHON_BIN") or sys.executable
        # The runner writes its JSON payload to this file rather than stdout, so a
        # C-extension or fd-1 write from the tests cannot corrupt the protocol.
        handle, out_path = tempfile.mkstemp(prefix="mint-trace-", suffix=".json")
        os.close(handle)
        env["MINT_TRACE_OUTPUT"] = out_path
        try:
            completed = _run_capture(
                [interpreter, "-c", _TRACE_RUNNER],
                cwd=context.root,
                env=env,
                timeout=timeout,
            )
            try:
                data = json.loads(Path(out_path).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {
                    "exitCode": completed.returncode or 1,
                    "coveredLines": 0,
                    "totalLines": 0,
                    "files": [],
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        data.setdefault("stderr", completed.stderr)
        data["target"] = target
        runs.append(data)

    failed = [run for run in runs if run.get("exitCode") != 0]
    if failed:
        return {
            "exitCode": int(failed[0].get("exitCode", 1)),
            "coveredLines": 0,
            "totalLines": 0,
            "files": [],
            "stdout": "\n".join(str(run.get("stdout", "")) for run in failed),
            "stderr": "\n".join(str(run.get("stderr", "")) for run in failed),
        }

    totals: dict[str, int] = {}
    covered: dict[str, set[int]] = {}
    for run in runs:
        for file_data in run.get("files", []):
            relpath = str(file_data["path"])
            totals[relpath] = int(file_data["totalLines"])
            covered.setdefault(relpath, set()).update(int(line) for line in file_data["coveredLineNumbers"])

    files = [
        {
            "path": relpath,
            "coveredLines": len(covered.get(relpath, set())),
            "totalLines": total,
            "percent": (len(covered.get(relpath, set())) / total * 100.0) if total else 100.0,
        }
        for relpath, total in sorted(totals.items())
    ]
    return {
        "exitCode": 0,
        "coveredLines": sum(item["coveredLines"] for item in files),
        "totalLines": sum(item["totalLines"] for item in files),
        "files": files,
        "stdout": "\n".join(str(run.get("stdout", "")) for run in runs),
        "stderr": "\n".join(str(run.get("stderr", "")) for run in runs),
    }


_TRACE_RUNNER = r'''
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

generated = Path(os.environ["MINT_GENERATED_DIR"]).resolve()
conformance = Path(os.environ["MINT_CONFORMANCE_DIR"]).resolve()
src = generated / "src"
pythonpath = [str(src)]
sys.path.insert(0, str(src))
for extra in os.environ.get("MINT_REQUIRED_SRC", "").split(os.pathsep):
    if extra:
        pythonpath.append(extra)
        sys.path.insert(0, extra)
if os.environ.get("PYTHONPATH"):
    pythonpath.append(os.environ["PYTHONPATH"])
os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath)
os.chdir(generated)
target = os.environ.get("MINT_TRACE_TARGET", "unit")
pytest_args = ["tests", "-q"] if target == "unit" else [str(conformance), "-q"]

stdout = io.StringIO()
stderr = io.StringIO()
counts = {}
src_resolved = src.resolve()


def line_counter(relpath):
    def count_line(frame, event, arg):
        if event == "line":
            key = (relpath, frame.f_lineno)
            counts[key] = counts.get(key, 0) + 1
        return count_line

    return count_line


def count_generated_calls(frame, event, arg):
    if event != "call":
        return None
    try:
        path = Path(frame.f_code.co_filename).resolve()
        relpath = path.relative_to(src_resolved).as_posix()
    except (OSError, ValueError):
        return None
    if path.name == "_mint_provenance.py":
        return None
    return line_counter(relpath)


previous_trace = sys.gettrace()
sys.settrace(count_generated_calls)
try:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = pytest.main(pytest_args)
finally:
    sys.settrace(previous_trace)

def executable_lines(source_text, filename):
    # Bytecode-backed executable lines via code.co_lines(): excludes blank lines,
    # comments, docstrings, string continuations, and bare else:/finally: headers
    # that the naive "non-blank, non-#" heuristic wrongly counted as executable.
    try:
        code = compile(source_text, filename, "exec")
    except SyntaxError:
        return None
    lines = set()
    stack = [code]
    code_type = type(code)
    while stack:
        current = stack.pop()
        for start, end, lineno in current.co_lines():
            if lineno:
                lines.add(lineno)
        for const in current.co_consts:
            if isinstance(const, code_type):
                stack.append(const)
    return lines


files = []
covered_total = 0
line_total = 0
for path in sorted(src.rglob("*.py")):
    if path.name == "_mint_provenance.py":
        continue
    rel = path.relative_to(src).as_posix()
    source_text = path.read_text(encoding="utf-8", errors="ignore")
    executable = executable_lines(source_text, str(path))
    if executable is None:
        continue
    covered = {
        lineno
        for (relpath, lineno), count in counts.items()
        if relpath == rel and count > 0
    }
    covered_executable = executable & covered
    if executable:
        files.append({
            "path": rel,
            "coveredLines": len(covered_executable),
            "totalLines": len(executable),
            "percent": len(covered_executable) / len(executable) * 100.0,
            "coveredLineNumbers": sorted(covered_executable),
        })
        covered_total += len(covered_executable)
        line_total += len(executable)

payload = json.dumps({
    "exitCode": int(exit_code),
    "coveredLines": covered_total,
    "totalLines": line_total,
    "files": files,
    "stdout": stdout.getvalue(),
    "stderr": stderr.getvalue(),
}, sort_keys=True)
out_path = os.environ.get("MINT_TRACE_OUTPUT")
if out_path:
    with open(out_path, "w", encoding="utf-8") as _fh:
        _fh.write(payload)
else:
    print(payload)
'''


def _run_mutation_probe(
    context: Any,
    *,
    required_src: list[Path],
    baseline_already_passed: bool = False,
) -> dict[str, Any]:
    if not context.config.test_quality.mutation_probe:
        return {"status": "skipped", "reason": "mutationProbe is false"}

    if not baseline_already_passed:
        baseline = _run_mutation_baseline(context, required_src=required_src)
        if baseline["status"] == "failed":
            return baseline

    candidates = _mutation_candidates(context.generated_dir / "src")
    max_candidates = context.config.test_quality.mutation_max_candidates
    tested = candidates[:max_candidates]
    survivors: list[dict[str, Any]] = []

    for candidate in tested:
        killed = _mutate_and_test(context, candidate, required_src=required_src)
        if not killed:
            survivors.append(
                {
                    "path": candidate.relpath,
                    "name": candidate.name,
                    "line": candidate.lineno,
                }
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


def _run_mutation_baseline(context: Any, *, required_src: list[Path]) -> dict[str, Any]:
    unit_result = _run_project_script(context, context.config.scripts.unit, required_src)
    conf_result = _run_project_script(context, context.config.scripts.conformance, required_src)
    failures: list[str] = []
    if unit_result.returncode != 0:
        failures.append(f"unit script exited {unit_result.returncode}")
    if conf_result.returncode != 0:
        failures.append(f"conformance script exited {conf_result.returncode}")
    if not failures:
        return {"status": "passed"}
    return {
        "status": "failed",
        "reason": "mutation baseline test run failed: " + ", ".join(failures),
        "candidateCount": 0,
        "testedCandidates": 0,
        "survivors": [],
        "baseline": {
            "unit": {
                "exitCode": unit_result.returncode,
                "stdout": unit_result.stdout,
                "stderr": unit_result.stderr,
            },
            "conformance": {
                "exitCode": conf_result.returncode,
                "stdout": conf_result.stdout,
                "stderr": conf_result.stderr,
            },
        },
    }


def _mutation_candidates(src_dir: Path) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    if not src_dir.exists():
        return candidates
    for path in sorted(src_dir.rglob("*.py")):
        if path.name == "_mint_provenance.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        relpath = path.relative_to(src_dir).as_posix()
        candidates.extend(_candidates_from_body(path, relpath, tree.body, []))
    return sorted(candidates, key=lambda item: (item.relpath, item.lineno, item.name))


def _candidates_from_body(
    path: Path,
    relpath: str,
    body: list[ast.stmt],
    parents: list[str],
) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for node in body:
        if isinstance(node, ast.ClassDef):
            candidates.extend(_candidates_from_body(path, relpath, node.body, parents + [node.name]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") or not node.body:
                continue
            first = node.body[0]
            last = node.body[-1]
            # Skip single-line bodies (`def f(): return x`): our replacement is
            # line-based and rewriting `def`+body on one line would corrupt the file.
            if first.lineno == node.lineno:
                continue
            end_lineno = getattr(last, "end_lineno", last.lineno)
            name = ".".join(parents + [node.name]) if parents else node.name
            candidates.append(
                MutationCandidate(
                    path=path,
                    relpath=relpath,
                    name=name,
                    lineno=node.lineno,
                    body_start=first.lineno,
                    body_end=end_lineno,
                    # col_offset preserves the exact leading whitespace (tabs or
                    # spaces) so the injected raise keeps the block's indentation.
                    body_col=first.col_offset,
                )
            )
    return candidates


def _mutate_and_test(
    context: Any,
    candidate: MutationCandidate,
    *,
    required_src: list[Path],
) -> bool:
    original = candidate.path.read_text(encoding="utf-8")
    try:
        candidate.path.write_text(_mutated_source(original, candidate), encoding="utf-8")
        _remove_runtime_caches(context.generated_dir)
        _remove_runtime_caches(context.conformance_dir)
        unit_result = _run_project_script(context, context.config.scripts.unit, required_src)
        if unit_result.returncode != 0:
            return True
        conf_result = _run_project_script(context, context.config.scripts.conformance, required_src)
        return conf_result.returncode != 0
    finally:
        candidate.path.write_text(original, encoding="utf-8")
        _remove_runtime_caches(context.generated_dir)
        _remove_runtime_caches(context.conformance_dir)


def _mutated_source(source: str, candidate: MutationCandidate) -> str:
    lines = source.splitlines(keepends=True)
    first_body_line = lines[candidate.body_start - 1]
    # Reproduce the exact indentation (tabs or spaces) from the AST column offset,
    # falling back to the leading whitespace run if the line is shorter than expected.
    indent = first_body_line[: candidate.body_col] or first_body_line[
        : len(first_body_line) - len(first_body_line.lstrip())
    ]
    replacement = f'{indent}raise AssertionError("mint mutation probe: {candidate.name}")\n'
    lines[candidate.body_start - 1 : candidate.body_end] = [replacement]
    return "".join(lines)


def _run_project_script(
    context: Any,
    script: str,
    required_src: list[Path],
) -> subprocess.CompletedProcess[str]:
    # Delegate to the canonical helper in stacks (single source of truth): it adds the
    # existence/executable-bit checks and the subprocess timeout that this copy lacked.
    return _stacks_run_project_script(context, script, env=_script_env(context, required_src))


def _script_env(context: Any, required_src: list[Path]) -> dict[str, str]:
    return _stacks_script_env(context, required_src)


def _remove_runtime_caches(path: Path) -> None:
    if not path.exists():
        return
    for cache_dir in path.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in path.rglob(".pytest_cache"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in path.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)
