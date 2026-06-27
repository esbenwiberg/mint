# Plain Forge Practice

`plain-forge` is an npm package that installs agent skills and rules. It does
not render code. Its job is to make agents good spec authors.

## Installed Shape

The CLI supports agent layouts:

- `.claude/`
- `.codex/`
- `.forgecode/`
- `.agents/`

It installs:

- `skills/`
- `rules/`
- optional `docs/`

It tracks exactly what it wrote in `.plain-forge/manifest.json` so update and
uninstall can avoid deleting user content.

## Core Workflow Skills

Important skills:

- `forge-plain`
  - Full new-project interview.
  - Writes specs incrementally.
  - Phases: product behavior, technology, testing, validation.

- `init-plain-project`
  - Lightweight skeleton.
  - Creates `template/base.plain`, a stub top module, scripts, and config.

- `add-feature`
  - Adds one feature to existing specs.
  - Reads the target module and its import/requires chain.
  - Asks one writable question at a time.
  - Writes after every answer.

- `plain-healthcheck`
  - Verifies configs and dry-runs top modules.
  - Final gate after spec edits.

- `run-codeplain`
  - Supervises a live render.
  - Watches `codeplain.log`.
  - Detects retry loops and pathologies.
  - Routes fixes back to spec-edit skills.

- `debug-specs`
  - Reads generated code as evidence.
  - Diagnoses whether the real fix is ambiguous spec, missing spec, conflict,
    incorrect spec, or missing implementation requirement.
  - Edits only `.plain` files, never generated code.

## The One-Question Loop

The repeated pattern in `forge-plain` and `add-feature` is:

1. Ask one focused question.
2. User answers.
3. Immediately write a spec snippet.
4. Review the new snippet.
5. Ask the next question.

This matters because the spec becomes the conversation's visible memory. The
agent should not hold a huge invisible design in context and dump it later.

Good questions are "writable":

- "When title is empty, should the request return 400 or use a default?"
- Not "How should validation work?"

If an answer is vague, the next question drills into the same topic. It still
asks only one thing.

## Rules That Keep Specs Renderable

The rules under `forge/rules/` encode the discipline:

- `definitions.md`
  - Concept syntax, uniqueness, predefined concepts, no cycles.

- `func-specs.md`
  - Functional specs are chronological, language-agnostic, unambiguous, and
    capped at about 200 changed LOC.

- `impl-reqs.md`
  - Implementation reqs are about HOW, not WHAT.
  - Unit-test requirements live here.

- `test-reqs.md`
  - Conformance-test requirements live here.
  - Unit-test requirements do not.

- `import-modules.md`
  - Import modules live under `template/` and contain no functional specs.

- `requires-modules.md`
  - Requires is a build chain and should only connect matching stacks.

- `linked-resources.md`
  - Resources are local text files and should be linked once.

- `line-length.md`
  - Plain lines should stay under 120 characters.
  - Use nested bullets, not bare continuation lines.

## Healthcheck Practice

`plain-healthcheck` is the cheap gate before spending render credits.

It should:

- inventory `.plain` modules
- identify top modules
- pair each top module with a config
- validate config YAML
- check script paths and executable bits
- run `codeplain <top>.plain --dry-run`
- report PASS or FAIL first

The important habit is that every spec-finalizing workflow ends with
healthcheck.

## Render Supervision Practice

`run-codeplain` treats `codeplain.log` as ground truth.

It tracks:

- current functionality
- last completed functionality
- current attempt count

It classifies retry loops:

- honest convergence
- under-specified spec
- renderer drifting from the spec
- broken test script or environment

When it stops a render, the fix goes to specs or scripts, not generated code.
Then healthcheck runs again, and rendering resumes with `--render-from` or
`--render-range`.

This supervisor pattern is worth copying even if we do not copy Plain exactly.

