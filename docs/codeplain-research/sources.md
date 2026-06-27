# Sources

## Product And Article

- Codeplain website: https://www.codeplain.ai/
- Codeplain pricing page: https://www.codeplain.ai/pricing
- The New Stack article:
  https://thenewstack.io/codeplain-spec-driven-regenerative-code/
- Plain language docs: https://plainlang.org/

## Public Repositories

- plain-forge: https://github.com/Codeplain-ai/plain-forge
- codeplain client: https://github.com/Codeplain-ai/codeplain

## Public Website Facts Captured

From the Codeplain site:

- Codeplain describes itself as turning Plain specifications into
  production-ready software.
- It frames specs as the source of truth.
- It says Codeplain auto-generates unit and conformance tests.
- The pricing page defines the rendering credit around a successfully rendered
  functionality.

From The New Stack article:

- Codeplain was founded in early 2025 and launched quietly in September 2025.
- Codeplain announced `plain-forge` as an open-source agentic skills framework.
- Incode is cited as a customer using Codeplain for external data provider
  integrations.
- The article frames the approach as regenerative software: specs are preserved,
  code is regenerated.

## plain-forge Files Inspected

Repository cloned temporarily to:

```text
/private/tmp/plain-forge-codeplain
```

High-value files:

- `README.md`
- `CLAUDE.md`
- `package.json`
- `bin/cli.mjs`
- `forge/skills/forge-plain/SKILL.md`
- `forge/skills/add-feature/SKILL.md`
- `forge/skills/run-codeplain/SKILL.md`
- `forge/skills/debug-specs/SKILL.md`
- `forge/skills/plain-healthcheck/SKILL.md`
- `forge/skills/init-plain-project/SKILL.md`
- `forge/skills/create-import-module/SKILL.md`
- `forge/skills/create-requires-module/SKILL.md`
- `forge/skills/load-plain-reference/SKILL.md`
- `forge/rules/*.md`

## codeplain Client Files Inspected

Repository cloned temporarily to:

```text
/private/tmp/codeplain-client
```

High-value files:

- `README.md`
- `plain2code.py`
- `plain2code_arguments.py`
- `module_renderer.py`
- `partial_rendering.py`
- `change_detection.py`
- `plain_modules.py`
- `plain_file.py`
- `plain_spec.py`
- `codeplain_REST_api.py`
- `git_utils.py`
- `render_machine/code_renderer.py`
- `render_machine/state_machine_config.py`
- `render_machine/render_context.py`
- `render_machine/actions/render_functional_requirement.py`
- `render_machine/actions/run_unit_tests.py`
- `render_machine/actions/fix_unit_tests.py`
- `render_machine/actions/render_conformance_tests.py`
- `render_machine/actions/run_conformance_tests.py`
- `render_machine/actions/fix_conformance_test.py`
- `render_machine/actions/analyze_specification_ambiguity.py`
- `render_machine/actions/prepare_repositories.py`
- `render_machine/actions/commit_implementation_code_changes.py`
- `render_machine/actions/finish_functional_requirement.py`

## Notes On Evidence Strength

Strong evidence:

- Public client code proves FRID rendering, render ranges, metadata hashes,
  Git checkpoints, local test scripts, conformance loops, and API payload shape.
- plain-forge skill files prove the spec-authoring practice and rules.
- The product site and pricing page confirm public positioning and billing unit.

Weaker evidence:

- Hosted server prompts, exact model choices, and production scaling practices
  are not visible in the public repos.
- The article gives customer and economics claims, but those are reported claims,
  not independently verifiable from code.

