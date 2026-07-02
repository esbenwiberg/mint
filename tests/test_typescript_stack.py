from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mint_cli.errors import MintError
from mint_cli import workflow
from mint_cli.renderer import RecordingClient, ReplayClient, ScriptedModelClient, build_prompt
from mint_cli.renderer.base import RenderRequest


TS_SPEC = """---
module: calc-ts
description: TypeScript calculator library
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Add: adds two numbers.
- Subtract: subtracts one number from another.
## implementation
- Expose add(a, b) and sub(a, b) from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: add returns the sum
  spec:
    - add(a, b) returns a + b.
  acceptance:
    - add(2, 3) returns 5.
- id: FR2
  title: sub returns the difference
  spec:
    - sub(a, b) returns a - b.
  acceptance:
    - sub(10, 3) returns 7.
"""


CORE_SPEC = """---
module: math-core
description: TypeScript math core
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Double: doubles a number.
## implementation
- Expose double(n) from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: double returns twice the input
  spec:
    - double(n) returns n * 2.
  acceptance:
    - double(4) returns 8.
"""


USES_CORE_SPEC = """---
module: uses-core
description: TypeScript module requiring math-core
imports: []
requires: [math-core]
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Quadruple: doubles a doubled number.
## implementation
- Expose quadruple(n) from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: quadruple returns four times the input
  spec:
    - quadruple(n) returns n * 4 using math-core.
  acceptance:
    - quadruple(3) returns 12.
"""


# A Python stand-in for the `node` TypeScript-compiler finder. It mirrors the real
# finder's contract: read MINT_TS_SRC, emit one JSON record per exported function
# body span with character offsets just inside the surrounding braces.
_FINDER_STUB = r"""
import json, os, re, sys
from pathlib import Path

src = Path(os.environ["MINT_TS_SRC"])
pattern = re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^{]+)?{")
out = []
for path in sorted(src.rglob("*.ts")):
    if path.name.endswith(".d.ts"):
        continue
    text = path.read_text(encoding="utf-8")
    for match in pattern.finditer(text):
        name = match.group(1)
        if name.startswith("_"):
            continue
        brace = match.end() - 1
        depth, i = 0, brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(
            {
                "file": str(path),
                "name": name,
                "bodyStart": brace + 1,
                "bodyEnd": i,
                "line": text[:brace].count("\n") + 1,
            }
        )
sys.stdout.write(json.dumps(out))
"""


# A Python stand-in for the `node` TypeScript-compiler signature finder. Mirrors the
# real finder's contract: read MINT_TS_SRC, emit one JSON record per exported function
# signature with { name, params:[{name,type}], returnType, source }.
_SIGNATURE_FINDER_STUB = r"""
import json, os, re, sys
from pathlib import Path

src = Path(os.environ["MINT_TS_SRC"])


def split_top_level(text):
    parts, depth, start, quote = [], 0, 0, None
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(pairs.values())
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in pairs:
            depth += 1
        elif ch in closers and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p for p in parts if p.strip()]


def parse_params(raw):
    out = []
    for item in split_top_level(raw):
        item = item.strip()
        name, _, typ = item.partition(":")
        name = name.strip().rstrip("?").strip()
        if name.startswith("..."):
            name = name[3:].strip()
        out.append({"name": name, "type": typ.strip() or None})
    return out


def find_paren(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


out = []
pattern = re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
for path in sorted(list(src.rglob("*.ts")) + list(src.rglob("*.tsx"))):
    if path.name.endswith(".d.ts"):
        continue
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(src).as_posix()
    for match in pattern.finditer(text):
        name = match.group(1)
        open_idx = match.end() - 1
        close_idx = find_paren(text, open_idx)
        if close_idx < 0:
            continue
        params = parse_params(text[open_idx + 1:close_idx])
        rest = text[close_idx + 1:]
        ret = None
        rmatch = re.match(r"\s*:\s*([^{;\n]+)", rest)
        if rmatch:
            ret = rmatch.group(1).strip()
        out.append({"name": name, "params": params, "returnType": ret, "source": rel})
sys.stdout.write(json.dumps(out))
"""


def install_ts_tool_stubs(root: Path, monkeypatch) -> Path:
    bin_dir = root / "tool-bin"
    bin_dir.mkdir()
    log = root / "ts-tool-log.txt"

    (bin_dir / "tsc").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s %s\\n' 'tsc' \"$*\" >> {log}\n",
        encoding="utf-8",
    )
    # The vitest stub emulates the real tooling contract used by the test-quality gate:
    #   * --coverage writes an istanbul coverage-final.json (full unless MINT_FAKE_FULL_COVERAGE=0)
    #   * a mutated source body (contains the probe marker) fails the run, so mutations are
    #     "killed" — unless MINT_FAKE_MUTATION_SURVIVES=1 forces a survivor.
    (bin_dir / "vitest").write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"printf '%s %s\\n' 'vitest' \"$*\" >> {log}\n"
        'if grep -rqs "mint mutation probe" src 2>/dev/null; then\n'
        '  if [ "${MINT_FAKE_MUTATION_SURVIVES:-0}" = "1" ]; then exit 0; fi\n'
        '  echo "mutation killed" >&2; exit 1\n'
        "fi\n"
        'reports=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in --coverage.reportsDirectory=*) reports="${arg#*=}";; esac\n'
        "done\n"
        'if [ -n "$reports" ]; then\n'
        '  mkdir -p "$reports"\n'
        '  cov="${MINT_FAKE_FULL_COVERAGE:-1}"\n'
        '  json="{"\n'
        "  first=1\n"
        '  while IFS= read -r f; do\n'
        '    abs="$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"\n'
        '    if [ "$cov" = "1" ]; then s=\'{"0":1,"1":1}\'; else s=\'{"0":1,"1":0}\'; fi\n'
        '    if [ $first -eq 0 ]; then json="$json,"; fi\n'
        "    first=0\n"
        '    json="$json\\"$abs\\":{\\"path\\":\\"$abs\\",\\"s\\":$s,\\"statementMap\\":{}}"\n'
        '  done < <(find src -name "*.ts" ! -name "*.d.ts" 2>/dev/null)\n'
        '  json="$json}"\n'
        '  printf "%s" "$json" > "$reports/coverage-final.json"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "tsc").chmod(0o755)
    (bin_dir / "vitest").chmod(0o755)

    finder = bin_dir / "ts_finder.py"
    finder.write_text(_FINDER_STUB, encoding="utf-8")

    # Stand-in for the `node` TS-compiler signature finder (production uses the TS AST;
    # here typescript is not installed). Mirrors its contract: read MINT_TS_SRC and
    # emit one JSON record per exported function signature.
    sig_finder = bin_dir / "ts_sig_finder.py"
    sig_finder.write_text(_SIGNATURE_FINDER_STUB, encoding="utf-8")

    # Stand-in for `npm install`: real npm would hit the network. Mint only requires
    # that node_modules exist after the install step, so create it.
    install_stub = bin_dir / "npm_install_stub.py"
    install_stub.write_text(
        "import os\nos.makedirs('node_modules', exist_ok=True)\n", encoding="utf-8"
    )

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("MINT_TS_MUTATION_FINDER_COMMAND", f"{sys.executable} {finder}")
    monkeypatch.setenv("MINT_TS_SIGNATURE_FINDER_COMMAND", f"{sys.executable} {sig_finder}")
    monkeypatch.setenv("MINT_TS_INSTALL_COMMAND", f"{sys.executable} {install_stub}")
    return log


def disable_test_quality(root: Path) -> None:
    """Switch off the test-quality gate for structural tests that don't exercise it."""
    config = root / "mint.yaml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("  enabled: true\n", "  enabled: false\n"), encoding="utf-8")


def ts_patch(
    module: str,
    unit: str,
    source: str,
    *,
    extra_unit_test: str = "",
    tsconfig_options: dict | None = None,
    conformance_contents: str = "import { describe, expect, it } from 'vitest';\n",
    extra_files: list[dict] | None = None,
) -> str:
    compiler_options = tsconfig_options or {
        "target": "ES2022",
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "strict": True,
    }
    files = [
        {"path": ".gitignore", "action": "write", "contents": "node_modules/\n.vite/\n.vitest/\ncoverage/\n"},
        {
            "path": "package.json",
            "action": "write",
            "contents": json.dumps(
                {
                    "name": module,
                    "type": "module",
                    "scripts": {
                        "typecheck": "tsc --noEmit",
                        "test:unit": "vitest run tests",
                        "test:conformance": "vitest run",
                    },
                    "devDependencies": {"typescript": "^5.0.0", "vitest": "^3.0.0"},
                },
                indent=2,
            )
            + "\n",
        },
        {
            "path": "tsconfig.json",
            "action": "write",
            "contents": json.dumps(
                {
                    "compilerOptions": compiler_options,
                    "include": ["src/**/*.ts", "tests/**/*.ts"],
                },
                indent=2,
            )
            + "\n",
        },
        {"path": "src/index.ts", "action": "write", "contents": source},
        {
            "path": f"tests/{unit.lower()}.test.ts",
            "action": "write",
            "contents": f"import {{ describe, expect, it }} from 'vitest';\n{extra_unit_test}\n",
        },
        {
            "path": f"{unit}/{unit.lower()}.test.ts",
            "action": "write",
            "contents": conformance_contents,
            "root": "conformance",
        },
    ]
    files.extend(extra_files or [])
    return json.dumps({"summary": f"{module} {unit}", "files": files})


def calc_ts_response(request: RenderRequest) -> str:
    if request.current_unit_id == "FR1":
        return ts_patch(
            request.module,
            "FR1",
            "export function add(a: number, b: number): number { return a + b; }\n",
            extra_unit_test="import { add } from '../src/index';\n"
            "describe('add', () => { it('adds', () => expect(add(2, 3)).toBe(5)); });\n",
        )
    return ts_patch(
        request.module,
        "FR2",
        "export function add(a: number, b: number): number { return a + b; }\n"
        "export function sub(a: number, b: number): number { return a - b; }\n",
        extra_unit_test="import { sub } from '../src/index';\n"
        "describe('sub', () => { it('subtracts', () => expect(sub(10, 3)).toBe(7)); });\n",
    )


def test_typescript_model_render_runs_typecheck_vitest_and_reports(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    log = install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.chdir(project.root)

    client = ScriptedModelClient(calc_ts_response)
    status, output = workflow.render_module("calc-ts", model_client=client)

    assert status == 0, output
    assert "RENDER calc-ts" in output
    assert client.calls == [("FR1", "unit", 1), ("FR2", "unit", 1)]
    meta = project.metadata("calc-ts")
    assert meta["lastSuccessfulUnitId"] == "FR2"
    assert all(record["testQuality"]["status"] == "passed" for record in meta["functionalUnits"])
    attempts = project.root / ".mint" / "generated" / "calc-ts" / ".mintgen" / "attempts" / "FR1"
    assert (attempts / "unit-1.patch.json").is_file()
    assert (attempts / "unit-1.stdout.log").is_file()
    assert (project.root / ".mint" / "generated" / "calc-ts" / ".mintgen" / "render.log").is_file()
    report = json.loads(
        (project.root / ".mint" / "generated" / "calc-ts" / ".mintgen" / "reports" / "latest.json").read_text()
    )
    assert report["units"][0]["attemptManifests"]
    tool_log = log.read_text(encoding="utf-8")
    assert "tsc --noEmit" in tool_log
    assert "vitest run tests" in tool_log
    assert "--config" in tool_log
    assert "vitest.conformance.config.ts" in tool_log
    assert str(project.root / "conformance" / "calc-ts") in tool_log
    config_text = (
        project.root / ".mint" / "generated" / "calc-ts" / "vitest.conformance.config.ts"
    ).read_text(encoding="utf-8")
    assert f"root: {json.dumps(str(project.root))}" in config_text
    # Glob is derived from the configured conformance dir (conformanceDir/module),
    # not hardcoded, so a non-default conformanceDir still matches.
    assert "include: ['conformance/calc-ts/**/*.test.ts']" in config_text
    assert f"{json.dumps('calc-ts')}: " in config_text


def test_typescript_test_quality_reports_coverage_and_mutation(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.chdir(project.root)

    status, output = workflow.render_module(
        "calc-ts", model_client=ScriptedModelClient(calc_ts_response)
    )

    assert status == 0, output
    fr2 = project.metadata("calc-ts")["functionalUnits"][1]["testQuality"]
    assert fr2["status"] == "passed"
    assert fr2["coverage"]["status"] == "passed"
    assert fr2["coverage"]["percent"] >= 60
    assert fr2["mutation"]["status"] == "passed"
    assert fr2["mutation"]["testedCandidates"] >= 1
    assert all(item["status"] == "passed" for item in fr2["traceability"])
    fr1 = project.metadata("calc-ts")["functionalUnits"][0]["testQuality"]
    assert fr1["coverage"]["status"] == "skipped"
    assert "deferred until final functional unit" in fr1["coverage"]["reason"]


def test_typescript_test_quality_fails_on_low_coverage(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.setenv("MINT_FAKE_FULL_COVERAGE", "0")
    monkeypatch.chdir(project.root)

    status, output = workflow.render_module(
        "calc-ts", model_client=ScriptedModelClient(calc_ts_response)
    )

    assert status == 1
    assert "test-quality gate failed" in output
    assert "coverage" in output
    coverage = project.metadata("calc-ts")["functionalUnits"][1]["testQuality"]["coverage"]
    assert coverage["status"] == "failed"
    assert coverage["percent"] < 60


def test_typescript_test_quality_fails_when_mutation_survives(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.setenv("MINT_FAKE_MUTATION_SURVIVES", "1")
    monkeypatch.chdir(project.root)

    status, output = workflow.render_module(
        "calc-ts", model_client=ScriptedModelClient(calc_ts_response)
    )

    assert status == 1
    assert "test-quality gate failed" in output
    mutation = project.metadata("calc-ts")["functionalUnits"][1]["testQuality"]["mutation"]
    assert mutation["status"] == "failed"
    assert mutation["survivors"]


def test_typescript_adapter_rejects_explicit_signature_drift(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec(
        "matcher",
        """---
module: matcher
description: matcher rules
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Resource: resource record.
## implementation
- Expose `matchByDefinition(resource, contract, fields): boolean` from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: match by definition
  spec:
    - `matchByDefinition(resource, contract, fields): boolean` returns whether the resource satisfies the contract.
  acceptance:
    - matchByDefinition({ id: "r1" }, { id: "c1" }, []) returns true.
""",
    )
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        return ts_patch(
            request.module,
            "FR1",
            "export type Resource = { id: string };\n"
            "export function matchByDefinition(resources: Resource[]): Resource[] { return resources; }\n",
            extra_unit_test=(
                "import { matchByDefinition } from '../src/index';\n"
                "describe('matchByDefinition', () => { it('runs', () => expect(matchByDefinition([])).toEqual([])); });\n"
            ),
        )

    status, output = workflow.render_module("matcher", model_client=ScriptedModelClient(response))

    assert status == 1
    assert "TypeScript signature mismatch for `matchByDefinition`" in output
    assert "expected 3 params but generated 1" in output


def test_typescript_signature_check_scopes_to_rendered_units(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec(
        "match",
        """---
module: match
description: multi-function matcher
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Lookup: normalized text key.
## implementation
- Expose matcher helpers from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: normalize lookup text
  spec:
    - `normalizeLookup(value): string` lowercases and trims text.
  acceptance:
    - normalizeLookup(" North ") returns "north".
- id: FR2
  title: read a resource field
  spec:
    - `resourceFieldValue(resource, field): string | undefined` returns the field value.
  acceptance:
    - resourceFieldValue({ name: "Ada" }, "name") returns "Ada".
""",
    )
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        return ts_patch(
            request.module,
            "FR1",
            "export function normalizeLookup(value: unknown): string { return String(value).trim().toLowerCase(); }\n",
            extra_unit_test=(
                "import { normalizeLookup } from '../src/index';\n"
                "describe('normalizeLookup', () => { it('normalizes', () => expect(normalizeLookup(' North ')).toBe('north')); });\n"
            ),
        )

    status, output = workflow.render_module(
        "match",
        unit_range="FR1:FR1",
        model_client=ScriptedModelClient(response),
    )

    assert status == 0, output
    assert "resourceFieldValue" not in output


def test_typescript_signature_check_ignores_prose_with_parentheses(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec(
        "choices",
        """---
module: choices
description: choice helper
imports: []
requires: []
stack: typescript-lib
rendererProvider: model
rendererModel: mock-ts-model
rendererPromptVersion: ts-v1
---

## definitions
- Choice: selected lookup key.
## implementation
- Expose choose from src/index.ts.
- Use package.json scripts with tsc --noEmit and Vitest.
## test
- Unit and conformance tests use Vitest.
## functional
- id: FR1
  title: choose fallback
  spec:
    - Otherwise (choice/lookup): return the fallback value.
  acceptance:
    - choose("", "fallback") returns "fallback".
""",
    )
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        return ts_patch(
            request.module,
            "FR1",
            "export function choose(choice: string, fallback: string): string { return choice || fallback; }\n",
            extra_unit_test=(
                "import { choose } from '../src/index';\n"
                "describe('choose', () => { it('falls back', () => expect(choose('', 'fallback')).toBe('fallback')); });\n"
            ),
        )

    status, output = workflow.render_module("choices", model_client=ScriptedModelClient(response))

    assert status == 0, output
    assert "Otherwise" not in output


def test_typescript_test_quality_hard_fails_without_coverage_tooling(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    log = install_ts_tool_stubs(project.root, monkeypatch)
    # Replace the vitest stub with one that never emits a coverage report and reports the
    # missing provider, like real vitest without @vitest/coverage-v8 installed.
    (project.root / "tool-bin" / "vitest").write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"printf '%s %s\\n' 'vitest' \"$*\" >> {log}\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in --coverage) echo "Cannot find dependency @vitest/coverage-v8" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (project.root / "tool-bin" / "vitest").chmod(0o755)
    monkeypatch.chdir(project.root)

    status, output = workflow.render_module(
        "calc-ts", model_client=ScriptedModelClient(calc_ts_response)
    )

    assert status == 1
    assert "test-quality gate failed" in output
    fr1_coverage = project.metadata("calc-ts")["functionalUnits"][0]["testQuality"]["coverage"]
    assert fr1_coverage["status"] == "skipped"
    coverage = project.metadata("calc-ts")["functionalUnits"][1]["testQuality"]["coverage"]
    assert coverage["status"] == "failed"
    assert "@vitest/coverage-v8" in coverage["reason"]


def test_typescript_adapter_normalizes_tsconfig_module_resolution(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("math-core", CORE_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        return ts_patch(
            request.module,
            "FR1",
            "export function double(n: number): number { return n * 2; }\n",
            tsconfig_options={
                "target": "ES2022",
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "strict": True,
            },
        )

    status, output = workflow.render_module("math-core", model_client=ScriptedModelClient(response))

    assert status == 0, output
    tsconfig = json.loads(
        (project.root / ".mint" / "generated" / "math-core" / "tsconfig.json").read_text(
            encoding="utf-8"
        )
    )
    assert tsconfig["compilerOptions"]["moduleResolution"] == "Bundler"
    assert tsconfig["compilerOptions"]["module"] == "ESNext"


def test_typescript_adapter_rewrites_conformance_relative_src_imports(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("math-core", CORE_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        return ts_patch(
            request.module,
            "FR1",
            "export function double(n: number): number { return n * 2; }\n",
            conformance_contents=(
                "import { describe, expect, it } from 'vitest';\n"
                "import { double } from '../../src/index';\n"
            ),
        )

    status, output = workflow.render_module("math-core", model_client=ScriptedModelClient(response))

    assert status == 0, output
    conformance = project.root / "conformance" / "math-core" / "FR1" / "fr1.test.ts"
    assert "from 'math-core'" in conformance.read_text(encoding="utf-8")


def test_typescript_rerenders_from_changed_functional_unit(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.chdir(project.root)
    assert workflow.render_module("calc-ts", model_client=ScriptedModelClient(calc_ts_response))[0] == 0
    before = project.metadata("calc-ts")
    fr1_commit = before["functionalUnits"][0]["finishedCommit"]
    fr2_commit = before["functionalUnits"][1]["finishedCommit"]

    spec_path = project.spec_path("calc-ts")
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "sub(a, b) returns a - b.",
            "sub(a, b) returns the arithmetic value a - b.",
        ),
        encoding="utf-8",
    )
    client = ScriptedModelClient(calc_ts_response)
    status, output = workflow.render_module("calc-ts", model_client=client)

    assert status == 0, output
    assert "Range: FR2:FR2" in output
    assert client.calls == [("FR2", "unit", 1)]
    after = project.metadata("calc-ts")
    assert after["functionalUnits"][0]["finishedCommit"] == fr1_commit
    assert after["functionalUnits"][1]["finishedCommit"] != fr2_commit


def test_typescript_shared_section_change_forces_full_rerender(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    monkeypatch.chdir(project.root)
    assert workflow.render_module("calc-ts", model_client=ScriptedModelClient(calc_ts_response))[0] == 0

    spec_path = project.spec_path("calc-ts")
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "Expose add(a, b) and sub(a, b) from src/index.ts.",
            "Expose add(a, b) and sub(a, b) from the public src/index.ts barrel.",
        ),
        encoding="utf-8",
    )
    client = ScriptedModelClient(calc_ts_response)
    status, output = workflow.render_module("calc-ts", model_client=client)

    assert status == 0, output
    assert "non-functional spec changed" in output
    assert "Range: FR1:FR2" in output
    assert client.calls == [("FR1", "unit", 1), ("FR2", "unit", 1)]


def test_typescript_required_module_wiring_is_explicit(make_project, monkeypatch):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("math-core", CORE_SPEC)
    project.write_spec("uses-core", USES_CORE_SPEC)
    install_ts_tool_stubs(project.root, monkeypatch)
    disable_test_quality(project.root)
    monkeypatch.chdir(project.root)

    def response(request: RenderRequest) -> str:
        if request.module == "math-core":
            return ts_patch(
                "math-core",
                "FR1",
                "export function double(n: number): number { return n * 2; }\n",
            )
        return ts_patch(
            "uses-core",
            "FR1",
            "import { double } from 'math-core';\n"
            "export function quadruple(n: number): number { return double(double(n)); }\n",
        )

    status, output = workflow.render_module("uses-core", model_client=ScriptedModelClient(response))

    assert status == 0, output
    package = json.loads(
        (project.root / ".mint" / "generated" / "uses-core" / "package.json").read_text(encoding="utf-8")
    )
    assert package["dependencies"]["math-core"] == "file:../math-core"


def test_typescript_prompt_uses_stack_hints_and_typescript_fence():
    prompt = build_prompt(
        RenderRequest(
            module="uses-core",
            stack="typescript-lib",
            template=None,
            spec_ir={},
            definitions=[],
            implementation=[],
            test=[],
            imported_context=[],
            required_modules=[
                {
                    "module": "math-core",
                    "files": [
                        {
                            "path": "src/index.ts",
                            "contents": "export function double(n: number) { return n * 2; }\n",
                            "language": "typescript",
                        }
                    ],
                }
            ],
            units_so_far=[],
            current_unit={"id": "FR1", "title": "quadruple", "spec": [], "acceptance": []},
            prompt_hints=["package.json scripts must include `tsc --noEmit` and Vitest."],
            code_fence_language="typescript",
        ),
        "ts-v1",
    )

    assert "## Stack adapter guidance" in prompt
    assert "tsc --noEmit" in prompt
    assert "```typescript" in prompt
    assert "export function double" in prompt


def test_typescript_healthcheck_missing_replay_fails_loudly(make_project):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)

    status, output = workflow.healthcheck_module("calc-ts", root=project.root)

    assert status == 1
    assert "Replay cassettes missing for model renderer spec calc-ts" in output
    assert "MINT_LIVE=1 mint live-smoke calc-ts" in output


def test_typescript_healthcheck_partial_generated_repo_points_to_clean(make_project):
    project = make_project(provider="model", model="mock-ts-model", prompt_version="ts-v1")
    project.write_spec("calc-ts", TS_SPEC)
    generated = project.root / ".mint" / "generated" / "calc-ts"
    generated.mkdir(parents=True)

    status, output = workflow.healthcheck_module(
        "calc-ts",
        root=project.root,
        allow_missing_replay=True,
    )

    assert status == 1
    assert "Generated repo is missing metadata" in output
    assert "mint clean calc-ts --yes" in output


def test_typescript_stale_replay_cassette_fails_with_live_smoke_hint(tmp_path):
    request = RenderRequest(
        module="calc-ts",
        stack="typescript-lib",
        template=None,
        spec_ir={},
        definitions=[],
        implementation=[],
        test=[],
        imported_context=[],
        required_modules=[],
        units_so_far=[],
        current_unit={"id": "FR1", "title": "add", "spec": [], "acceptance": []},
        prompt_hints=["package.json scripts must include `tsc --noEmit` and Vitest."],
        code_fence_language="typescript",
    )
    response = json.dumps(
        {"summary": "ok", "files": [{"path": "package.json", "action": "write", "contents": "{}"}]}
    )
    RecordingClient(
        ScriptedModelClient({"default": response}),
        cassette_dir=tmp_path,
        model="mock-ts-model",
        prompt_version="ts-v1",
    ).complete(system="system", prompt="old prompt", request=request)

    replay = ReplayClient(cassette_dir=tmp_path, model="mock-ts-model", prompt_version="ts-v1")

    with pytest.raises(MintError, match="prompt content changed.*MINT_LIVE=1 mint live-smoke calc-ts"):
        replay.complete(system="system", prompt="new prompt", request=request)


# --------------------------------------------------------------------------- #
# Adapter-level defect regression tests (no full render required)
# --------------------------------------------------------------------------- #

from mint_cli import stacks
from mint_cli.stacks import (
    TypeScriptStackAdapter,
    _probe_binary,
    _run_capture,
    _strip_jsonc,
    _ts_mutated_source,
    _vitest_test_count,
)


def _write_package(tmp_path: Path, scripts: dict) -> Path:
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"name": "m", "scripts": scripts}), encoding="utf-8")
    return package


def test_package_scripts_reject_pass_with_no_tests(tmp_path):
    package = _write_package(
        tmp_path,
        {
            "typecheck": "tsc --noEmit",
            "test:unit": "vitest run --passWithNoTests tests",
            "test:conformance": "vitest run",
        },
    )
    failures = TypeScriptStackAdapter()._package_script_failures(package)
    assert any("--passWithNoTests" in f for f in failures)


def test_package_scripts_reject_shell_metacharacters(tmp_path):
    package = _write_package(
        tmp_path,
        {
            "typecheck": "tsc --noEmit",
            "test:unit": "vitest run tests || true",
            "test:conformance": "vitest run && echo done",
        },
    )
    failures = TypeScriptStackAdapter()._package_script_failures(package)
    assert any("metacharacter" in f and "test:unit" in f for f in failures)
    assert any("metacharacter" in f and "test:conformance" in f for f in failures)


def test_package_scripts_accept_clean_commands(tmp_path):
    package = _write_package(
        tmp_path,
        {
            "typecheck": "tsc --noEmit",
            "test:unit": "vitest run tests",
            "test:conformance": "vitest run",
        },
    )
    assert TypeScriptStackAdapter()._package_script_failures(package) == []


def test_vitest_test_count_reads_report_and_missing(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"numTotalTests": 4}), encoding="utf-8")
    assert _vitest_test_count(report) == 4
    report.write_text(json.dumps({"numTotalTests": 0}), encoding="utf-8")
    assert _vitest_test_count(report) == 0
    # A missing/unparsable report is "unknown", never silently zero.
    assert _vitest_test_count(tmp_path / "nope.json") is None


def test_strip_jsonc_handles_comments_and_trailing_commas():
    text = """{
      // line comment
      "compilerOptions": {
        "strict": true, /* block */
        "target": "ES2022",
      },
    }"""
    parsed = json.loads(_strip_jsonc(text))
    assert parsed["compilerOptions"]["target"] == "ES2022"
    assert parsed["compilerOptions"]["strict"] is True
    # A URL-like value containing // inside a string must survive.
    assert json.loads(_strip_jsonc('{"u": "http://x/y"}'))["u"] == "http://x/y"


def test_probe_binary_missing_does_not_raise():
    result = _probe_binary("definitely-not-a-real-binary-xyz")
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_run_capture_times_out_and_reports():
    result = _run_capture(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=0.2,
    )
    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_cleanup_runtime_caches_spares_src_coverage(tmp_path):
    root = tmp_path / "gen"
    (root / "src" / "coverage").mkdir(parents=True)
    (root / "src" / "coverage" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "coverage").mkdir()
    (root / "coverage" / "junk.txt").write_text("x", encoding="utf-8")
    (root / ".vitest").mkdir()

    TypeScriptStackAdapter().cleanup_runtime_caches(root)

    # Top-level caches removed; a legitimate src/coverage module is preserved.
    assert not (root / "coverage").exists()
    assert not (root / ".vitest").exists()
    assert (root / "src" / "coverage" / "index.ts").exists()


def test_ts_mutated_source_offsets_survive_astral_chars():
    # An emoji (astral char, 2 UTF-16 units) in a comment before the body would shift
    # code-point based slicing; the UTF-16 approach must place the throw correctly.
    source = 'export function f(): number {\n  // \U0001F600 comment\n  return 1;\n}\n'
    units = source.encode("utf-16-le")
    open_brace = source.index("{")
    close_brace = source.rindex("}")
    # Offsets are UTF-16 code units, matching what the TS compiler emits.
    body_start = len(source[:open_brace].encode("utf-16-le")) // 2 + 1
    body_end = len(source[:close_brace].encode("utf-16-le")) // 2
    candidate = {"name": "f", "bodyStart": body_start, "bodyEnd": body_end}
    mutated = _ts_mutated_source(source, candidate)
    assert "mint mutation probe: f" in mutated
    # Braces preserved and original body removed.
    assert mutated.startswith("export function f(): number {")
    assert mutated.rstrip().endswith("}")
    assert "return 1;" not in mutated
