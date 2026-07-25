# CIDA Runtime Dependency Policy

## Objective

CIDA runtime code must remain small, auditable, deterministic, and safe to run in restricted environments. Runtime dependencies are intentionally limited to standard libraries plus one explicit tokenizer exception.

## Runtime Allowlist

- Go standard library
- Python standard library
- `tiktoken`

`tiktoken` is the only approved external runtime exception. Package managers may install the transitive packages required for `tiktoken` to work, but CIDA application code must not import those transitive packages directly.

## Development And CI Dependencies

The following packages are development and CI tools only:

- `pytest`
- `hypothesis`
- `pytest-cov`
- `pytest-mock`
- `ruff`
- `mypy`

They are listed through `requirements-dev.txt` and `requirements-ci.txt`, not `requirements-runtime.txt`.

## Frontmatter Format

CIDA supports a restricted frontmatter subset implemented with Python stdlib in `FrontmatterCodec`: key/value mappings, strings, quoted strings, booleans, null, numbers, inline lists, indented lists, nested maps, nested lists, and multi-field maps inside lists. It rejects duplicate keys, anchors, aliases, merge keys, custom tags, multiple documents, block scalars, inline maps, unsafe object construction, excessive depth, excessive key count, and ambiguous syntax.

## Exception Process

New runtime dependencies require an explicit policy update, a security and determinism rationale, tests proving runtime imports are allowed intentionally, and maintainer approval before merge.

## Vendoring

Vendoring, copying, or embedding third-party libraries to bypass this policy is prohibited unless the same exception process approves it explicitly.

## Automated Verification

CI runs `python scripts/check_runtime_dependencies.py` on Ubuntu and Windows. The gate parses runtime Python files with `ast`, permits stdlib/local modules plus `tiktoken`, and fails on direct imports of any other external module.
