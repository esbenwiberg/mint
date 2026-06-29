from __future__ import annotations

import json
import os
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


def install_ts_tool_stubs(root: Path, monkeypatch) -> Path:
    bin_dir = root / "tool-bin"
    bin_dir.mkdir()
    log = root / "ts-tool-log.txt"
    for name in ["tsc", "vitest"]:
        path = bin_dir / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s %s\\n' '{name}' \"$*\" >> {log}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def ts_patch(module: str, unit: str, source: str, *, extra_unit_test: str = "") -> str:
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
                    "compilerOptions": {
                        "target": "ES2022",
                        "module": "ESNext",
                        "moduleResolution": "Bundler",
                        "strict": True,
                    },
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
            "contents": "import { describe, expect, it } from 'vitest';\n",
            "root": "conformance",
        },
    ]
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
    assert all(record["testQuality"]["status"] == "skipped" for record in meta["functionalUnits"])
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
    assert str(project.root / "conformance" / "calc-ts") in tool_log


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
