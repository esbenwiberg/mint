---
module: tasklist
description: Task-list CLI built on the shared taskstore library
imports: [taskstore]
requires: [taskstore]
stack: python-cli
template: tasklist
---

## definitions

- Command: a subcommand of the `tasklist-cli` console program.

## implementation

- Use Python 3.12.
- Provide a console command named `tasklist-cli`.
- Persist tasks via the required `taskstore` library, not a private store.
- The store path comes from the `TASKLIST_STORE` environment variable.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests drive the CLI as a subprocess, never private helpers.

## functional

- id: FR1
  title: Add prints the new task as incomplete
  spec:
    - `tasklist-cli add "Buy milk"` stores the task via taskstore.
    - The command prints the task with an incomplete marker `[ ]`.
  acceptance:
    - After `tasklist-cli add "Buy milk"`, `tasklist-cli list` shows `[ ] Buy milk`.

- id: FR2
  title: List shows tasks in insertion order
  spec:
    - `tasklist-cli list` prints all stored tasks.
    - Tasks appear in the order they were added.
  acceptance:
    - After adding "Buy milk" then "Write notes", list output shows "Buy milk" before "Write notes".
