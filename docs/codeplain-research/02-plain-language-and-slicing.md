# Plain Language And Slicing

## File Shape

A `.plain` file is a module. It contains YAML frontmatter followed by structured
sections. The important sections are:

- `***definitions***`
  - Defines concepts such as `:User:` or `:API:`.
  - Concepts must be defined before use.
  - Concept names are globally unique across the module and imports.

- `***implementation reqs***`
  - Describes how the software should be built.
  - Holds language, framework, architecture, runtime, coding conventions, and
    unit-test requirements.
  - In plain-forge rules, everything about `:UnitTests:` belongs here.

- `***test reqs***`
  - Describes conformance-test requirements.
  - Holds framework, execution command, fixtures, network policy, and constraints
    for `:ConformanceTests:`.

- `***functional specs***`
  - Describes observable behavior.
  - Rendered incrementally, top to bottom.
  - Each bullet is a functionality, or FRID in the client.

- `***acceptance tests***`
  - Nested under one functional spec.
  - Adds concrete end-to-end verification scenarios for that functionality.

## Concept Discipline

Plain uses `:ConceptName:` references as durable vocabulary. This helps the
renderer keep entities, APIs, test surfaces, and architectural components
consistent across specs.

The public rules stress:

- Define before use.
- Avoid circular concept references.
- Do not redefine predefined concepts such as `:Implementation:`,
  `:UnitTests:`, or `:ConformanceTests:`.
- Keep functional specs language-agnostic.
- Put implementation choices in implementation reqs, not functional specs.

## `import` Boundaries

`import` is for shared context. An imported module:

- Lives conventionally under `template/`.
- May contain definitions, implementation reqs, and test reqs.
- Must not contain functional specs.
- Must not use `requires`.

This is how Codeplain avoids duplicating shared stack rules, schemas, and domain
definitions. It also creates clean reuse without copying generated code.

## `requires` Boundaries

`requires` is for a build chain between functional modules. A required module:

- Is rendered before the dependent module.
- Can contain functional specs.
- Provides prior functional specs as context.
- Provides generated code as the starting point for the dependent module.
- Exposes only concepts listed in `exported_concepts`.

The public rules say `requires` should only connect modules with the same
language, framework, and runtime because the generated code is copied forward.
A React frontend should not `requires` a Python backend. Their shared contract
should be a resource such as OpenAPI, not a generated-code chain.

## Functional Specs As Rewrite Slices

Each functional spec becomes a renderable unit. The client assigns FRIDs by
position in the spec tree:

- `1`
- `2`
- `3`

Nested sections can produce dotted IDs, but the core idea is the same: each
functionality is addressable.

This solves the "where do you slice the rewrite?" question:

- The author decides module boundaries.
- The author keeps each functional spec small.
- The renderer stores metadata for each FRID.
- Partial render starts at the first affected FRID.
- Dependent modules after a changed module can be wiped and re-rendered.

## Chronological Rendering

Functional specs are rendered in order. While rendering functionality `N`, the
renderer has no knowledge of future functionality `N+1`.

This is a big constraint. It forces specs to be chronological and incremental:

- Earlier specs must establish foundations.
- Later specs can build on earlier specs.
- A spec cannot assume future behavior.
- Reordering specs can change generated output.

## Linked Resources

Plain can link local text files as resources. These are included in renderer
context. Public rules are strict:

- Resources are local files, not URLs.
- Resources are text, not binaries.
- Resources are files, not directories.
- `.plain` files are not linked as resources; use `import` or `requires`.

Structured protocol artifacts should live in `resources/`, for example:

- OpenAPI specs
- JSON Schema
- GraphQL SDL
- Protobuf files
- Example payloads

This is especially important for integrations. The spec should reference a
canonical artifact, not restate schema details in prose.

