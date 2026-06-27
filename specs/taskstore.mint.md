---
module: taskstore
description: Shared storage library for task lists
imports: []
requires: []
stack: python-lib
template: taskstore
---

## definitions

- Task: an item with text and a completed flag.
- TaskStore: a file-backed, ordered collection of Tasks.

## implementation

- Use Python 3.12.
- Expose a small library API under `src/taskstore/`.
- The public API is `Task` and `TaskStore`; persistence format is JSON.
- Unit tests use pytest.

## test

- Conformance tests use pytest.
- Conformance tests call the public library API only, never private helpers.

## functional

- id: FR1
  title: Adding a task stores it as incomplete
  spec:
    - `TaskStore.add_task(text)` appends a Task with that text.
    - A newly added Task has `completed` set to False.
  acceptance:
    - After `add_task("Buy milk")`, `list_tasks()` returns one incomplete Task with that text.

- id: FR2
  title: Listing preserves insertion order
  spec:
    - `TaskStore.list_tasks()` returns all stored Tasks.
    - Tasks are returned in the order they were added.
  acceptance:
    - After adding "Buy milk" then "Write notes", `list_tasks()` returns them in that order.
