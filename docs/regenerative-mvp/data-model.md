# Data Model

## Spec File

Spec files use Markdown with YAML frontmatter:

```md
---
module: todo-cli
description: Task list CLI and library
imports: []
requires: []
stack: python-cli
---

## definitions

- Task: item with text and a completed flag.

## implementation

- Use Python 3.12.
- Expose a small library API under `src/todo_cli/`.
- Provide a console command named `todo`.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call the public library API or CLI, not private helpers.

## functional

- id: FR1
  title: Add creates a task
  spec:
    - Calling `todo add "Buy milk"` stores a Task with that text.
    - Newly added tasks are incomplete.
  acceptance:
    - After adding "Buy milk", `todo list` shows it as incomplete.
```

The parser may accept friendly Markdown at first, but it must emit the canonical
IR below.

## Canonical Spec IR

```json
{
  "module": "todo-cli",
  "description": "Task list CLI and library",
  "imports": [],
  "requires": [],
  "stack": "python-cli",
  "definitions": [
    {
      "name": "Task",
      "text": "item with text and a completed flag."
    }
  ],
  "implementation": [
    "Use Python 3.12.",
    "Expose a small library API under `src/todo_cli/`.",
    "Provide a console command named `todo`.",
    "Unit tests use pytest."
  ],
  "test": [
    "Conformance tests use pytest.",
    "Conformance tests call the public library API or CLI, not private helpers."
  ],
  "functionalUnits": [
    {
      "id": "FR1",
      "title": "Add creates a task",
      "spec": [
        "Calling `todo add \"Buy milk\"` stores a Task with that text.",
        "Newly added tasks are incomplete."
      ],
      "acceptance": [
        "After adding \"Buy milk\", `todo list` shows it as incomplete."
      ],
      "resources": []
    }
  ]
}
```

## Unit IDs

MVP unit IDs are explicit strings matching:

```text
FR[0-9]+
```

Rules:

- IDs are unique within a module.
- IDs are ordered by file position.
- New units append by default.
- Reordering units is allowed but forces render from the earliest moved unit.
- Editing implementation/test/definition sections invalidates all units.

Later versions can support nested IDs, but not in the MVP.

## Hashes

Use SHA-256 over canonical JSON:

- `specHash`: entire canonical IR.
- `nonFunctionalSpecHash`: frontmatter, definitions, implementation, test,
  resources, imports, and requires, excluding functional unit text.
- `functionalUnits[].textHash`: one unit's spec and acceptance text.
- `generatedCodeHash`: hash of generated files excluding `.git` and `.mintgen`.

Hash canonicalization must sort object keys and normalize line endings.

## Module Metadata

Path:

```text
generated/<module>/.mintgen/module.json
```

Shape:

```json
{
  "version": 1,
  "module": "todo-cli",
  "specPath": "specs/todo-cli.mint.md",
  "renderId": "2026-06-27T12-00-00Z-todo-cli",
  "specHash": "...",
  "nonFunctionalSpecHash": "...",
  "importedContextHash": null,
  "requiredModuleCodeHash": null,
  "generatedCodeHash": "...",
  "lastSuccessfulUnitId": "FR1",
  "promptVersion": "v0",
  "model": "deterministic-python-cli-v0",
  "functionalUnits": [
    {
      "id": "FR1",
      "title": "Add creates a task",
      "textHash": "...",
      "status": "passed",
      "startedAt": "2026-06-27T12:00:00Z",
      "finishedAt": "2026-06-27T12:01:00Z",
      "implementationCommit": "...",
      "unitTestsCommit": "...",
      "conformanceCommit": "...",
      "finishedCommit": "...",
      "attempts": {
        "implementation": 1,
        "unit": 1,
        "conformance": 1
      }
    }
  ]
}
```

## Checkpoint Commits

Each generated module repo uses conventional messages:

```text
[mint] initial module: <module>
[mint] implemented <unit-id>: <title>
[mint] fixed unit tests for <unit-id>
[mint] generated conformance tests for <unit-id>
[mint] fixed conformance for <unit-id>
[mint] completed <unit-id>
```

Each commit body includes:

```text
Module: <module>
Unit: <unit-id>
Render-Id: <render-id>
Prompt-Version: <prompt-version>
Model: <model>
```

`mint render --from FR3` resets the nested repo to the commit before FR3 and
then renders FR3 and later units.

## Attempt Logs

Path:

```text
generated/<module>/.mintgen/attempts/<unit-id>/<attempt>.json
```

Shape:

```json
{
  "unitId": "FR1",
  "phase": "conformance",
  "attempt": 1,
  "promptPath": "prompt.md",
  "responsePath": "response.json",
  "patchPath": "patch.diff",
  "script": "test_scripts/run_conformance_tests.sh",
  "exitCode": 1,
  "stdoutPath": "stdout.log",
  "stderrPath": "stderr.log",
  "classification": "under_specified",
  "summary": "Expected task storage behavior is ambiguous."
}
```

The attempt log is evidence for `debug-specs`.

## Generated Code Ownership

Files under `generated/<module>/` are generated-owned. Human or agent edits are
allowed only through the renderer workflow.

Durable fixes belong in:

- `specs/*.mint.md`
- `resources/`
- `mint.yaml`
- `test_scripts/`
- renderer prompts/tools
