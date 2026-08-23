# ADR-002: Authority, Projections, and the MVP Review Surface

Status: accepted
Date: 2026-08-22

## Context

The system needs semantic graph operations without making generated structure impossible to
repair. Coding-agent users also need a review surface that works locally and is easy to inspect.

## Decision

Immutable observations and append-only memory events are authoritative. Claims, relations,
search indexes, embeddings, artifacts, and Markdown are derived projections. The MVP generates
an Obsidian-compatible Markdown vault for review, explanation, and navigation. Obsidian itself is
optional and never writes authoritative state directly.

## Alternatives

- Make the graph authoritative and reconstruct raw inputs from it.
- Make Markdown files authoritative.
- Build a custom web dashboard before dogfooding.

## Consequences

Projection rebuilds and residue audits become mandatory. Review edits must flow through explicit
MemoryGraph commands. We avoid an early frontend while preserving a human-readable local view.

## Compatibility and migration

Existing SQLite observations and events already satisfy the authority boundary. New projections
must carry source watermarks and may be regenerated without schema-breaking changes.

## Benchmark impact

Deletion tests cover every projection. The Markdown baseline is measured separately from the
graph-backed system so product gains are not attributed to formatting alone.
