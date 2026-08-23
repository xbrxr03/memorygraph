# ADR-003: MCP Transport and Agent Tool Surface

Status: accepted
Date: 2026-08-22

## Context

Codex and other coding agents need a small, portable integration boundary. Every additional tool
raises selection cost and expands the security surface.

## Decision

The MVP exposes a local STDIO MCP server with exactly five tools: `recall`, `record`, `explain`,
`correct`, and `forget`. Every request carries an explicit bank/workspace scope. Tool annotations
accurately distinguish read-only, mutating, and destructive operations. Server instructions keep
the core provenance and bounded-recall rules self-contained. Project-scoped Codex configuration
is supported, but installation never modifies user-global configuration silently.

## Alternatives

- A large one-tool-per-domain-operation MCP surface.
- HTTP-only transport.
- Host-specific shell hooks without MCP.

## Consequences

The five tools need stable envelopes and strong validation. Advanced graph operations remain in
the Python API and CLI. Host failures must fail open and never block the coding task.

## Compatibility and migration

The initial tool schemas are pre-v1. Breaking changes require a versioned server capability and
contract tests. HTTP can be added later without changing tool semantics.

## Benchmark impact

Protocol tests cover initialization, listing, invocation, bank isolation, destructive metadata,
bounded outputs, and malformed requests. Dogfood measures downstream task success, not MCP call
count alone.
