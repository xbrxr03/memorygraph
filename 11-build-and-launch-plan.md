# MemoryGraph Build and Launch Plan

**Status:** Approved; MVP execution in progress  
**Date:** August 22, 2026  
**Prerequisite:** Specifications `08`, `09`, and `10` approved on August 21, 2026  
**Rule:** Discoveries during implementation use tests and ADRs rather than silent design drift.

---

## 1. Outcome

Build an open-source, local-first memory revision layer that a developer can install in minutes, connect to a coding agent, and verify through an `explain` command and reproducible rot benchmark.

The launch is successful when users adopt it for recurring work—not when the repository only receives attention.

### Implementation snapshot — August 21, 2026

The embedded MVP now includes milestones 1–2, the provider-neutral and durable core of
milestones 4–5, and the public benchmark/chaos harness from milestone 7. It passes 12/12
public retrieval cases and 7/7 production chaos cases. Remaining launch-critical work is the
background worker loop, real provider adapters, hybrid retrieval, human-review execution,
MCP/coding-agent integrations, cross-platform CI, and design-partner dogfooding.

The August 22 research gate is now frozen into the build: raw observations/events remain the
authority; the graph is the revision/provenance projection; Dream is gated; recall is hybrid and
security-screened; the agent tool surface is five operations; Obsidian-compatible Markdown is the
first review surface; and task-success, deletion-residue, poisoning, and no-memory/Markdown/
Graphify baselines are release gates.

---

## 2. Delivery Strategy

Build vertical slices. Every milestone must produce a demonstrable user capability, tests, and a migration-safe database state.

Do not build the dashboard, cloud service, or broad adapter matrix before the temporal core and dream invariants are proven.

### Team assumption

The plan assumes one primary founder/maintainer using coding agents heavily. Calendar estimates are directional; exit criteria, not week numbers, determine progress.

---

## 3. Milestones

### Milestone 0: repository and contracts

**Target:** 2–3 days

Deliver:

- Repository skeleton.
- License decision and notices.
- Architecture docs copied from approved specifications.
- ADR template.
- Python/tooling configuration.
- CI across macOS, Linux, and Windows.
- Security policy and responsible disclosure.
- Initial example fixtures.

Exit:

- Clean install and test command on all supported OSes.
- No product code beyond validated skeleton and schemas.

### Milestone 1: deterministic evidence ledger

**Target:** Week 1

Deliver:

- SQLite connection policy and migrations.
- Banks.
- Immutable observations/chunks.
- Entities/aliases.
- Predicate registry.
- Claims/evidence/relations.
- Event log.
- Repository layer and domain models.
- JSONL export/import.

Demo:

```bash
memorygraph init
memorygraph bank create project:demo
memorygraph observe ...
memorygraph export --bank project:demo --format jsonl
```

Exit:

- Isolation, idempotency, evidence-span, and export round-trip tests pass.

### Milestone 2: temporal truth and explanation

**Target:** Week 2

Deliver:

- Explicit assert/confirm/supersede/contradict/retract.
- Bi-temporal current and as-of queries.
- Predicate-cardinality conflict rules.
- `explain` and `history`.
- FTS5 canonical text indexes.
- Deterministic MemoryRotBench fixtures.

Demo:

```bash
memorygraph history --subject Alice --predicate works_at
memorygraph explain CLAIM_ID
```

Exit:

- Golden state-change, historical mention, multi-value, and contested cases are exact.

### Milestone 3: retrieval

**Target:** Week 3

Deliver:

- Structured filters.
- FTS candidate retrieval.
- Optional embedding interface and local implementation.
- Entity and one-hop graph candidate retrieval.
- RRF fusion, temporal filtering, and token budgets.
- Evidence/context output modes.
- Retrieval benchmarks and ablations.

Exit:

- Hybrid improves development-set retrieval over FTS-only while exact-match regression stays within the approved tolerance.
- Vector-provider absence degrades gracefully.

### Milestone 4: inferred ingestion

**Target:** Week 4

Deliver:

- Extractor and resolver provider protocols.
- Structured-output schemas.
- Evidence-span validation.
- Entity candidate resolution.
- Durable operations/jobs and worker.
- Provider usage/cost traces.
- Raw versus inferred `remember` modes.

Exit:

- Model output can create no direct mutation.
- Malformed/provider-failed runs produce no partial data.
- All active extracted claims link to validated source spans.

### Milestone 5: dream maintenance

**Target:** Week 5

Deliver:

- Selector, comparison retrieval, relationship classifier.
- Challenger.
- Deterministic proposal validator.
- Atomic committer.
- Dry-run, review queue, and compensating rollback.
- Artifact refresh and citations.
- Dream reports and metrics.

Demo:

```bash
memorygraph dream --bank project:demo --dry-run
memorygraph review list --bank project:demo
memorygraph dream --bank project:demo
memorygraph rollback RUN_ID
```

Exit:

- All acceptance tests in `09-dream-cycle-protocol.md` pass.

### Milestone 6: coding-agent integration

**Target:** Week 6

Deliver:

- MCP tools/resources.
- Agent Skill.
- First adapters for Claude Code, Codex, and OpenCode.
- Installer, doctor, and uninstall.
- Fail-open hooks.
- Coding-agent capture policy.
- Flat memory-file importer.

Demo:

1. Install MemoryGraph into a real repository.
2. Work across multiple sessions.
3. Change a build procedure.
4. Ask the agent to perform the task.
5. Show correct current procedure and historical explanation.

Exit:

- Five internal multi-session dogfood projects.
- No adapter blocks host-agent work during service failure.

### Milestone 7: benchmark and hardening

**Target:** Weeks 7–8

Deliver:

- Public MemoryRotBench scenario set.
- External baseline adapters feasible within licenses/access.
- LongMemEval-V2 adapter.
- Poisoning/isolation suite.
- 100,000-claim performance run.
- Migration and crash-recovery tests.
- Benchmark report with failures and ablations.

Exit:

- v1 quality gates pass or documented failures trigger scope correction.
- Every launch number is reproducible from a public command.

### Milestone 8: design partners and launch

**Target:** Weeks 9–12, overlapping hardening

Deliver:

- Design-partner onboarding.
- Telemetry only by explicit opt-in.
- Issue templates that collect reproducible diagnostics safely.
- Quickstart, architecture animation/diagram, and 30-second demo.
- Migration guides.
- Public roadmap based on observed use.

Exit:

- At least five sustained weekly users or two teams replacing an existing memory workflow.
- At least three documented stale-memory failures prevented.
- Retention and usage evidence included in launch narrative.

---

## 4. Work Breakdown

### 4.1 Core epics

1. Persistence and migrations.
2. Temporal domain semantics.
3. Provenance and explanation.
4. Retrieval/indexing.
5. Provider abstraction.
6. Durable job system.
7. Dream validation and commit.
8. Security and privacy.
9. MCP/skills/adapters.
10. Benchmarking and observability.

### 4.2 Required architecture decision records

Create ADRs before changing:

- SQLite access library.
- Vector implementation.
- Default local embedding model.
- First supported LLM providers.
- Predicate registry extensibility.
- Event log/rollback mechanics.
- MCP transport and local service lifecycle.
- Package/distribution name.
- License.
- Any cloud or synchronization design.

ADR template:

```markdown
# ADR-NNN: Decision

Status: proposed | accepted | superseded
Date:

## Context
## Decision
## Alternatives
## Consequences
## Compatibility and migration
## Benchmark impact
```

---

## 5. Dependency Policy

Base installation should remain small:

```text
memorygraph
- Pydantic
- Typer
- platform-appropriate MCP dependency when MCP extra is selected
- no mandatory model SDK
- no mandatory vector extension
```

Suggested extras:

```text
memorygraph[local]     # local embeddings + sqlite vector adapter
memorygraph[openai]    # provider adapter
memorygraph[anthropic] # provider adapter
memorygraph[mcp]       # MCP server
memorygraph[server]    # local HTTP service
memorygraph[dev]       # test/lint/benchmark tooling
```

Lock application/dev environments. Library dependencies use compatible bounds and automated update testing.

---

## 6. Test Strategy

### Unit

- Temporal interval operations.
- Predicate conflict rules.
- Evidence span normalization.
- Rank fusion and budget selection.
- Authorization policies.
- Proposal validation.

### Property

- Banks never leak.
- As-of results obey interval semantics.
- Applying a valid supersession preserves history.
- Export/import round trip is invariant.
- Idempotent commands remain idempotent under retries.

### Integration

- SQLite migrations and crash recovery.
- FTS/vector fallback.
- Provider structured-output errors.
- Worker leasing/reclaim.
- MCP/CLI/API parity.
- Adapter install/uninstall.

### Golden model fixtures

Store provider inputs and outputs for deterministic boundary tests. Live model tests run separately and never gate every pull request.

### Security

- Direct/indirect prompt injection.
- Memory poisoning.
- Source spoofing.
- Oversized inputs.
- Path traversal in imports/exports.
- Cross-bank access.
- Secret redaction.
- Malicious provider output.

### Performance

- 1K, 10K, and 100K-claim fixtures.
- Concurrent reads plus dream writer.
- Index rebuild.
- Import/export throughput.

---

## 7. Developer Experience

### Seven-minute success path

```bash
uv tool install memorygraph
memorygraph init
memorygraph install --platform codex --project
memorygraph remember --bank project:auto --infer "We use pytest -n auto for the full suite."
memorygraph recall --bank project:auto "How do tests run?"
```

If provider configuration is missing, inferred commands explain the requirement and show the exact deterministic alternative.

### Repository presentation

The landing README should contain, in order:

1. One-sentence promise.
2. 20–30 second state-change demo.
3. Install command.
4. `explain` output.
5. Architecture in one diagram.
6. Honest benchmark table.
7. Integrations.
8. Security/local-first statement.
9. Deeper links.

Do not lead with a large competitor matrix or unverifiable “everyone else rots” claim.

### Worked example

Check in a tiny three-month fictional project history containing:

- Build backend migration.
- Historical reference to the old backend.
- Changed test command.
- Malicious instruction inside a dependency README.
- Final current/historical questions.

The entire example must run locally and generate deterministic expected outputs in explicit mode.

---

## 8. Open-Source and Community Strategy

### Principles

- Permissive core license if compatible with dependencies and business strategy.
- No contributor agreement surprise after adoption.
- Public roadmap and benchmark harness.
- Small, reviewable contributor modules.
- “Good first issue” tasks with deterministic fixtures.
- Release notes include migrations and behavior changes.
- Security reports handled privately.

### Architecture for contributors

The easiest contributions should be:

- New importer/exporter.
- New agent adapter.
- New provider adapter.
- New predicate pack.
- New benchmark scenario.
- Retrieval/reranking strategy.

Temporal invariants and commit logic remain a tightly reviewed core.

### Popularity versus adoption

Track separately:

- GitHub stars/forks/downloads.
- Successful installs.
- Banks active over multiple weeks.
- Recall usage.
- Claims with multiple supporting observations.
- Corrections/supersessions successfully handled.
- Returning design partners.

Stars are distribution evidence; repeated successful use is product evidence.

---

## 9. Design-Partner Program

Recruit 10–20 developers who:

- Use coding agents daily.
- Work repeatedly in the same repositories.
- Already maintain context or memory files.
- Can identify concrete stale-context failures.
- Will share anonymized failure descriptions and benchmarkable fixtures.

Weekly interview questions:

1. What did the agent have to relearn?
2. Which returned memory was wrong or irrelevant?
3. Which correction should have changed current belief?
4. Did `explain` make the result trustworthy?
5. Did capture feel invasive or noisy?
6. What would make removal painful?

Do not ask only whether they “like” the graph or UI.

---

## 10. Company and YC Narrative

### Problem

Persistent agent memory preserves statements but does not reliably manage revisions. As agents run longer, a retrieved item can be relevant and still be wrong.

### Insight

Retrieval and truth maintenance are different systems. Vectors find candidates; an evidence-backed temporal revision layer decides what is current and explains why.

### Wedge

Long-running coding agents, where project changes are frequent, sources are auditable, and stale memory causes visible repeated work or incorrect edits.

### Product proof

- Real multi-week usage.
- Prevented stale-memory failures.
- Evidence-backed explanations.
- Reproducible current/historical accuracy.
- Better accuracy-latency frontier on relevant benchmarks.

### Expansion

The same revision layer can serve personal assistants, support agents, workflow automation, and enterprise agent platforms. The durable asset is the belief/evidence/revision graph and its interoperability—not a single coding-agent plugin.

### Defensibility

- High-quality temporal/evidence dataset from real revisions, with consent.
- Predicate and resolution policies learned from production failure modes.
- Evaluation suite focused on belief maintenance and poisoning.
- Integration distribution across agent runtimes.
- Trust/debugging workflow and portable format.

Do not claim the graph schema itself is a moat.

---

## 11. Launch Assets

Required:

- Repository README.
- 30-second terminal/video demo.
- Interactive or static explanation trace.
- Public benchmark report.
- Architecture document.
- Threat model.
- Three integration guides.
- Worked example.
- Migration guide from flat files.
- Comparison page grounded in tested behavior.
- Launch essay: “Retrieval is not truth maintenance.”

Suggested headline:

> Your agent found a relevant memory. MemoryGraph tells it whether that memory is still true.

Suggested demo command:

```bash
memorygraph explain "Where does Alice work now?"
```

---

## 12. Stop/Go Gates

### Continue toward launch if

- Temporal explicit mode is exact.
- Inferred mode produces traceable claims with low false supersession.
- Users consult `explain` when state is disputed.
- Coding-agent integrations prevent repeated/stale-state failures.
- Hybrid retrieval plus revision beats retrieval-only baselines on the target scenarios.

### Narrow or pivot if

- Users value only search and never revisions/explanations.
- Model extraction costs dominate the benefit.
- False supersession cannot be pushed below the safety threshold.
- Host agents do not reliably invoke memory despite adapter hooks/skills.
- Flat Git-managed runbooks solve the target user’s problem just as well.

Possible narrow pivot:

> A temporal, evidence-backed project decision and runbook store for coding agents.

That would still use the same deterministic core.

---

## 13. Approval Checklist

Before implementation, approve or request changes to:

- [ ] Product wedge: long-running coding agents.
- [ ] Product promise: evidence-backed revision, not an absolute no-rot guarantee.
- [ ] Observation/entity/predicate/claim/evidence model.
- [ ] Bi-temporal semantics.
- [ ] SQLite + FTS5 authoritative local store.
- [ ] Optional vectors and model providers.
- [ ] Models-propose/policy-commits dream cycle.
- [ ] Directive privilege separation.
- [ ] MCP + Agent Skills integration base.
- [ ] MemoryRotBench and external benchmark policy.
- [ ] Milestone order.
- [ ] Open-source licensing direction.
- [ ] v1 non-goals.

Once these are approved, implementation can begin at Milestone 0 without reopening foundational architecture during routine coding.
