# MemoryGraph Architecture Index

**Date:** August 21, 2026  
**Current status:** Founder-approved; embedded MVP implementation is operational.

---

## Authoritative Specification Set

Read in this order:

1. [`07-intelligence-sweep.md`](07-intelligence-sweep.md) — verified patterns and lessons from current systems, benchmarks, and security research.
2. [`08-memorygraph-project-spec-v2.md`](08-memorygraph-project-spec-v2.md) — product definition, scope, domain model, temporal semantics, storage/retrieval architecture, APIs, integrations, security, and quality gates.
3. [`09-dream-cycle-protocol.md`](09-dream-cycle-protocol.md) — complete background-maintenance protocol, proposal/validation/commit boundary, review, rollback, budgets, metrics, and acceptance tests.
4. [`10-memoryrotbench-spec.md`](10-memoryrotbench-spec.md) — benchmark dataset, categories, metrics, baselines, anti-gaming rules, and launch thresholds.
5. [`11-build-and-launch-plan.md`](11-build-and-launch-plan.md) — milestone order, test strategy, design-partner program, open-source distribution, and YC/company narrative.
6. [`12-storage-schema-contract.md`](12-storage-schema-contract.md) — concrete SQLite DDL, indexes, state machines, and repository-enforced invariants.

Together these documents are the implementation contract. Architecture changes require an ADR.

---

## Research Archive

The pre-specification discovery files `01` through `06` were not recoverable after the
original workspace was lost. They were never implementation authority; the complete,
founder-approved contract begins at `07` and is preserved here.

---

## Frozen Core Decisions

- Product category: evidence-backed belief revision for agents.
- First wedge: long-running coding agents.
- Source of truth: immutable observations and atomic temporal claims.
- History: bi-temporal world-valid and system-record intervals.
- Local store: SQLite with FTS5.
- Semantic retrieval: optional vector index, never authoritative.
- Dream rule: models propose; deterministic policy validates and commits.
- Conflict behavior: predicate cardinality plus evidence authority; uncertainty may remain contested.
- Summaries: regenerable, citation-backed artifacts.
- Instructions: privileged directives separated from inferred memory.
- Integration: MCP and Agent Skills, then host-specific adapters.
- Proof: MemoryRotBench first, LongMemEval-V2 next.
- Architecture changes require explicit approval or an ADR; implementation follows the
  accepted safety boundary in specifications `08` through `12`.

---

## Approval Record

When approved, record:

```text
Approved by: Founder, through explicit build authorization in the project task
Approved at: 2026-08-21
Approved specification checkpoint: Recovered Dogfood Alpha source and executable contracts
Requested exceptions: Build the MVP through parallel agent orchestration
```

The original workspace predated Git initialization. Public Git history begins at the recovered,
verified Dogfood Alpha checkpoint.

The implemented slice includes the deterministic ledger, temporal graph, lexical recall,
provider-neutral dream runtime, durable operations, review routing, compensating rollback,
privacy deletion/recomputation, and executable MemoryRotBench retrieval and chaos suites.
