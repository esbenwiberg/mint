# Contributing

`mint` treats specs as source and generated code as disposable output.

Durable changes belong in:

- `.mint/specs/*.mint.md`
- `resources/`
- `mint.yaml`
- `test_scripts/`
- `src/mint_cli/`
- renderer prompts/tools once they exist

Do not patch files under `generated/<module>/` by hand. Inspect generated code
as evidence, then fix the spec, resources, config, scripts, or renderer.
