# ADR-001: Python package tooling

Status: accepted  
Date: 2026-08-21

## Context

The MVP needs a reproducible Python 3.11+ package, CLI entry point, and fast local test loop.

## Decision

Use a `src/` layout, Hatchling build backend, `uv` for development environments, Pydantic v2
at external schema boundaries, Typer for the CLI, pytest for tests, and Ruff for lint/format.
The provisional distribution name is `memorygraph-agent`; naming can change before public
release without changing the import package or persistence schema.

## Alternatives

Setuptools was viable but offers no advantage for this package. Poetry would couple project
management and build behavior more tightly than needed. A standard-library `argparse` CLI
would reduce one dependency but weaken generated help and subcommand ergonomics.

## Consequences

The base runtime has two Python dependencies. `uv` is recommended but not required for users.
Changing the public package name remains possible before v1.

## Compatibility and migration

No persistence impact.

## Benchmark impact

None.
