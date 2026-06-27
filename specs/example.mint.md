---
module: example
description: Example task-list CLI and library
imports: []
requires: []
stack: python-cli
---

## definitions

- Task: item with text and a completed flag.

## implementation

- Use Python 3.12.
- Expose a small library API under `src/example/`.
- Provide a console command named `example-todo`.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call the public library API or CLI, not private helpers.

## functional

- id: FR1
  title: Add creates a task
  spec:
    - Calling `example-todo add "Buy milk"` stores a Task with that text.
    - Newly added tasks are incomplete.
  acceptance:
    - After adding "Buy milk", `example-todo list` shows it as incomplete.

- id: FR2
  title: List shows tasks in insertion order
  spec:
    - Calling `example-todo list` prints all stored tasks.
    - Tasks appear in the order they were added.
  acceptance:
    - After adding "Buy milk" and then "Write notes", list output shows "Buy milk" first.
