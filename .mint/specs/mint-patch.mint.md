---
module: mint-patch
description: Self-hosted implementation of mint's renderer file-patch validation and application
imports: []
requires: []
stack: python-lib
rendererProvider: claude-cli
rendererModel: claude-fable-5-1
rendererPromptVersion: selfhost-patch-v1
---

## definitions

- Patch: a JSON object `{"summary": str, "files": [entry, ...]}` that a model renderer returns to describe file writes and deletes.
- Entry: one element of `files`, an object with `path` (relative POSIX path), `action` (`write` or `delete`, default `write`), `root` (`module` or `conformance`, default `module`), and `contents` (string, required when `action` is `write`).
- Root directory: the on-disk directory an entry's `root` maps to; `module` maps to the generated module directory and `conformance` maps to the conformance directory.
- Reserved component: a path component that is `.git` or `.mintgen`, which a patch may never write under at any depth.
- PatchError: the single exception type raised for every structural, path, or filesystem problem, carrying an actionable message.

## implementation

- Use Python 3.12 and only the standard library.
- Expose `validate_patch`, `apply_patch`, `PatchError`, `VALID_ACTIONS`, `VALID_ROOTS`, and `RESERVED_FIRST_COMPONENTS` from `src/mint_patch/`.
- `PatchError` subclasses `Exception` and is raised for every failure; never let `KeyError`, `TypeError`, `IsADirectoryError`, or a raw `OSError` escape the public functions.
- `validate_patch(patch)` accepts any Python value, raises `PatchError` on any structural problem, and returns a normalized copy: `{"summary": str, "files": [...]}` where `summary` is `str(patch.get("summary", ""))` and every normalized entry has exactly the keys `path`, `action`, `root`, plus `contents` only when `action` is `write`.
- `validate_patch` checks entries in order and reports the first problem; error messages name the entry as `files[<index>]` and include the offending value or the allowed alternatives.
- Path checks are performed with `pathlib.PurePosixPath` on the entry's `path`: reject absolute paths, paths whose normalized parts are empty (such as `.`), paths containing a `..` part, and paths containing a reserved component at any depth.
- `apply_patch(patch, module_dir, conformance_dir)` takes an already-validated patch and two `pathlib.Path` roots and returns a list of touched-path strings formatted as `<root>:<path>` for writes and `<root>:<path> (deleted)` for deletes, in patch order.
- `apply_patch` is atomic-ish: a pre-pass resolves every entry's target with `Path.resolve()` and verifies it lies strictly inside the resolved root directory before any file is written or deleted, so a single escaping entry (including an escape through a symlinked directory) leaves the filesystem untouched. A target that resolves to the root itself is rejected with a distinct message.
- Writes create parent directories as needed and write `contents` as UTF-8 text to the resolved target.
- Deletes operate on the unresolved target path so a symlink is removed as a symlink rather than following it: a symlink (including a dangling one) is unlinked, a directory is refused with `PatchError`, a regular file is unlinked, and a missing path is silently ignored.
- Unit tests use pytest and `tmp_path`.
- Do not refactor, rename, or add public functions beyond those listed; keep the module to the described surface.

## test

- Conformance tests use pytest and call only the public API.
- Conformance tests build patches and directories under `tmp_path`; never touch the repository.
- Include structural rejections, path-escape rejections, reserved-component rejections, a symlink escape blocked before any write, a broken-symlink delete, and a directory-delete refusal.

## functional

- id: FR1
  title: Validate patch structure and normalize entries
  spec:
    - `validate_patch` raises `PatchError` when the patch is not a dict, when `files` is missing, not a list, or empty, when an entry is not a dict, when `path` is missing, not a string, or blank, when `action` is not in `VALID_ACTIONS`, when `root` is not in `VALID_ROOTS`, or when a `write` entry lacks string `contents`.
    - Missing `action` defaults to `write` and missing `root` defaults to `module`; missing `summary` becomes an empty string.
    - A `delete` entry's normalized form has no `contents` key even if the input carried one.
  acceptance:
    - `validate_patch([1, 2])` raises `PatchError` whose message contains `must be a JSON object`.
    - `validate_patch({"summary": "x", "files": []})` raises `PatchError` whose message contains `non-empty 'files' list`.
    - `validate_patch({"files": [{"path": "a.py", "action": "chmod", "contents": "x"}]})` raises `PatchError` whose message contains `invalid action 'chmod'` and lists `delete, write`.
    - `validate_patch({"files": [{"path": "a.py", "root": "tests", "contents": "x"}]})` raises `PatchError` whose message contains `invalid root 'tests'`.
    - `validate_patch({"files": [{"path": "a.py", "action": "write"}]})` raises `PatchError` whose message contains `requires string 'contents'`.
    - `validate_patch({"files": [{"path": "a.py", "contents": "x"}, {"path": "b.py", "action": "delete", "contents": "ignored"}]})` returns `{"summary": "", "files": [{"path": "a.py", "action": "write", "root": "module", "contents": "x"}, {"path": "b.py", "action": "delete", "root": "module"}]}`.

- id: FR2
  title: Reject escaping and reserved paths at validation time
  spec:
    - `validate_patch` raises `PatchError` for an absolute path, for a path with a `..` part, for a path that normalizes to no parts, and for a path with `.git` or `.mintgen` as any component.
    - Error messages identify the entry index and the offending path.
  acceptance:
    - A `files[0]` entry with the absolute path `/usr/bin/evil.py` raises `PatchError` whose message contains `must be relative`.
    - A `files[0]` entry with path `../../evil.py` raises `PatchError` whose message contains `must not escape`.
    - A `files[0]` entry with path `.` raises `PatchError` whose message contains `empty path`.
    - A `files[0]` entry with path `.git/hooks/pre-commit` raises `PatchError` whose message contains `.git/`.
    - A `files[1]` entry with path `src/.git/config` raises `PatchError` whose message contains `files[1]` and `.git/`.
    - A `files[0]` entry with path `.mintgen/state.json` raises `PatchError` whose message contains `.mintgen/`.
    - A `files[0]` entry with path `src/pkg/module.py` is accepted.

- id: FR3
  title: Apply a validated patch atomically with containment checks
  spec:
    - `apply_patch` writes `write` entries under the matching root directory, creating parents, and deletes `delete` entries.
    - A pre-pass rejects any entry whose resolved target is outside its root directory before any entry is applied, including an escape via a symlinked directory inside the root.
    - Deleting a dangling symlink removes the symlink; deleting a directory raises `PatchError`; deleting a missing file is a no-op that is still reported as deleted.
    - Write failures surface as `PatchError` whose message starts with `Failed to write`.
  acceptance:
    - Applying `[{"path": "src/x.py", "contents": "print(1)\n"}, {"path": "FR1/test_fr1.py", "root": "conformance", "contents": "def test(): pass\n"}]` creates both files with the given text and returns `["module:src/x.py", "conformance:FR1/test_fr1.py"]`.
    - Applying `[{"path": "src/x.py", "action": "delete"}]` afterwards removes the file and returns `["module:src/x.py (deleted)"]`.
    - With `module/link` a symlink to a directory outside the module root, applying `[{"path": "ok.py", "contents": "x"}, {"path": "link/evil.py", "contents": "x"}]` raises `PatchError` whose message contains `escapes module dir` and `ok.py` does not exist afterwards.
    - With `module/dangling` a symlink to a missing target, applying `[{"path": "dangling", "action": "delete"}]` removes the symlink and returns `["module:dangling (deleted)"]`.
    - With `module/out` a symlink to a file outside the module root, applying `[{"path": "out", "action": "delete"}]` raises `PatchError` whose message contains `escapes module dir`; the symlink and the outside file both still exist afterwards, because containment is checked on the fully resolved target for deletes too.
    - With `module/self` a symlink to the module root itself, applying `[{"path": "self", "action": "delete"}]` raises `PatchError` whose message contains `itself` and the symlink still exists afterwards.
    - With `module/pkg` an existing directory, applying `[{"path": "pkg", "action": "delete"}]` raises `PatchError` whose message contains `cannot delete directory`.
