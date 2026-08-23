# MemoryRotBench Specification

**Status:** Proposed benchmark contract  
**Date:** August 21, 2026  
**Purpose:** Prove whether a memory system preserves useful, evidence-backed truth as state changes over time.

---

## 1. Why a New Benchmark

Existing benchmarks provide important coverage:

- [LongMemEval](https://github.com/xiaowu0162/longmemeval): extraction, multi-session reasoning, updates, temporal reasoning, and abstention.
- [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2/): environment state, dynamic tracking, workflow knowledge, gotchas, and premise awareness over agent trajectories.
- [LoCoMo](https://github.com/snap-research/locomo): long conversational QA and summarization.
- [BEAM](https://github.com/mohammadtavakoli78/BEAM): multiple memory abilities at contexts up to 10 million tokens.

MemoryRotBench does not replace them. It isolates the proposed product’s core claim: whether current beliefs remain correct, historical beliefs remain queryable, conflicts remain visible, and every answer remains grounded as observations accumulate.

---

## 2. Evaluation Layers

Scores are reported separately.

### 2.1 Construction

Did ingestion create the correct atomic claims, entities, evidence links, and temporal intervals?

### 2.2 Retrieval

Did recall return the gold evidence/claims inside a fixed result and token budget?

### 2.3 Belief maintenance

Did the system correctly preserve, confirm, supersede, contest, retract, or abstain as the timeline evolved?

### 2.4 Answering

Given retrieved context, did a fixed answer model respond correctly and cite the correct evidence?

### 2.5 Security

Did malicious or untrusted observations poison future recall, create directives, cross isolation boundaries, or cause unsafe procedural behavior?

### 2.6 Efficiency

What ingestion time/cost, storage, recall latency, dream time/cost, and answer-context tokens were required?

No single aggregate “memory score” replaces these layers in reports.

---

## 3. Dataset Format

Each scenario is a chronological event stream plus queries at checkpoints.

```json
{
  "scenario_id": "employment-historical-mention-001",
  "category": "state_update",
  "bank": "user:alice",
  "events": [
    {
      "event_id": "e1",
      "at": "2026-01-02T12:00:00Z",
      "kind": "message",
      "actor": "user",
      "content": "I work at Acme.",
      "gold_claims": ["alice works_at Acme [2026-01-02, 2026-03-04)"]
    },
    {
      "event_id": "e2",
      "at": "2026-03-04T12:00:00Z",
      "kind": "message",
      "actor": "user",
      "content": "I started at Stripe today.",
      "gold_claims": ["alice works_at Stripe [2026-03-04, null)"]
    },
    {
      "event_id": "e3",
      "at": "2026-04-10T12:00:00Z",
      "kind": "message",
      "actor": "user",
      "content": "My old Acme laptop had a great keyboard."
    }
  ],
  "queries": [
    {
      "after_event": "e3",
      "query": "Where does Alice work now?",
      "answer": "Stripe",
      "required_evidence": ["e2"],
      "forbidden_current_claims": ["Alice works at Acme"]
    },
    {
      "after_event": "e3",
      "query": "Where did Alice work in February?",
      "answer": "Acme",
      "required_evidence": ["e1"]
    }
  ]
}
```

Gold data includes:

- Canonical entities and permitted aliases.
- Atomic claims.
- World-valid intervals.
- Observation/source links.
- Expected claim relationships.
- Current winner or contested state at each checkpoint.
- Required, acceptable, and forbidden evidence.
- Whether abstention is required.

---

## 4. Scenario Categories

### 4.1 State replacement

- Employer, location, dependency version, project phase, active tool.
- Explicit and implied changes.
- Change effective immediately versus retroactively.

### 4.2 Historical mention trap

A newer observation mentions an old state without making it current.

Examples:

- “My old Acme laptop...”
- “The project used Poetry before the migration.”
- “When I lived in Boston...”

### 4.3 Multi-valued truth

New facts should coexist rather than supersede:

- Hobbies.
- Skills.
- Project contributors.
- Supported platforms.

### 4.4 Negation and correction

- “I don’t use X anymore.”
- “Correction: I meant Y, not X.”
- “That earlier date was wrong.”

### 4.5 Delayed reporting

Observation time differs from effective time:

- “I moved last month.”
- A tool discovers a commit made three weeks earlier.
- A document imported today describes a past event.

### 4.6 Conflicting sources

- User statement versus stale file.
- Tool output versus assistant inference.
- Two current authoritative files disagree.
- Equal-authority statements require contested output.

### 4.7 Stable facts

Facts that should not decay merely because time passes:

- Historical decisions.
- Birth dates.
- Commit hashes.
- Completed incident outcomes.

### 4.8 Expiring state

- Temporary credentials metadata without secret values.
- Sprint goal.
- Scheduled event.
- Sale/pricing fact with declared expiry.

### 4.9 Duplicate paraphrase

Repeated statements should strengthen evidence without producing retrieval pollution.

### 4.10 Entity ambiguity

- Two people with the same name.
- Product and repository sharing a name.
- File rename versus distinct new file.
- Project-relative paths across cloned machines.

### 4.11 Procedural learning

- A build command succeeds, later changes, and old command appears in historical logs.
- A recurring failure has a verified workaround.
- A workaround becomes obsolete after dependency upgrade.

### 4.12 Premise awareness

Queries contain a false assumption:

- “Why are we still using Poetry?” after the Hatchling migration.
- “Which Acme team is Alice on?” after leaving Acme.

### 4.13 Abstention

Evidence is insufficient, ambiguous, deleted, or contested.

### 4.14 Summary drift

Repeated artifact refreshes must remain grounded and must not invent or amplify claims.

### 4.15 Poisoning and injection

- User attempts to implant a privileged instruction as memory.
- Retrieved file contains “ignore previous instructions.”
- Successful-looking tool transcript contains a malicious procedure.
- Adversarial bridge text attempts to become relevant to a future target query.

### 4.16 Isolation

Similar names and facts exist in different banks. Retrieval and dreams must never cross the boundary.

### 4.17 Deletion and revocation

Evidence is deleted or its authority revoked; dependent belief state must be recomputed.

---

## 5. Scale Tiers

| Tier | Observations | Claims | Purpose |
|---|---:|---:|---|
| Tiny | 10–100 | 5–50 | Unit/golden correctness. |
| Small | 1,000 | 500 | CI retrieval and dream tests. |
| Medium | 10,000 | 5,000 | Local developer workload. |
| Large | 100,000 | 50,000 | v1 performance gate. |
| Stress | 1,000,000+ | 500,000+ | Informational, not initial launch blocker. |

Noise observations must include semantically similar distractors, not only random unrelated text.

---

## 6. Metrics

### 6.1 Construction metrics

- Entity precision/recall/F1.
- Claim precision/recall/F1.
- Predicate accuracy.
- Object normalization accuracy.
- Evidence-span exact/overlap F1.
- Valid-time boundary error.
- Duplicate rate.

### 6.2 Retrieval metrics

- Claim Recall@K.
- Evidence Recall@K.
- Precision@K.
- MRR and nDCG.
- Forbidden-current-claim rate.
- Citation correctness.
- Context tokens.

### 6.3 Maintenance metrics

- Current-state accuracy.
- Historical-state accuracy.
- Supersession precision/recall.
- Contradiction precision/recall.
- False supersession rate.
- False merge rate.
- Correct-contestation rate.
- Abstention accuracy.
- Unsupported active-claim rate.
- Reversibility success rate.

### 6.4 Security metrics

- Poison insertion success rate.
- Poison retrieval success rate.
- Attack action success rate.
- Unauthorized directive creation rate.
- Cross-bank leakage rate.
- Benign-memory rejection rate.
- Protected procedure false-admission rate.

### 6.5 Operational metrics

- Ingestion p50/p95/p99 latency.
- Recall p50/p95/p99 latency.
- Dream p50/p95 duration.
- Tokens and dollar cost per 1,000 observations.
- Database bytes per observation/claim.
- Peak memory.
- Failed/retried operation rate.
- Cold-start and index-rebuild time.

---

## 7. Baselines

Minimum baselines:

1. Full flat context within a fixed token budget.
2. Latest-N observations.
3. SQLite FTS5/BM25 only.
4. Dense retrieval only.
5. Hybrid FTS+dense without temporal belief maintenance.
6. MemoryGraph deterministic core.
7. MemoryGraph with inferred ingestion/dream.

External systems, subject to reproducible local/API access and license:

- Mem0.
- Graphiti.
- Hindsight.
- Cognee.

Each external baseline receives the same source stream, checkpoint timing, answer model, token budget, and latency reporting. System-specific strengths may be configured, but every divergence is documented.

---

## 8. Evaluation Protocol

### 8.1 Construction run

1. Initialize an empty isolated bank.
2. Ingest events chronologically.
3. Wait for or explicitly drain asynchronous work after each defined checkpoint.
4. Snapshot operation logs and costs.
5. Query structured belief state where the system exposes it.

### 8.2 Retrieval run

1. Issue each query at its checkpoint.
2. Request a fixed maximum number of items and tokens.
3. Save raw returned items and citations.
4. Grade retrieval without an answer model.

### 8.3 Answer run

1. Give a fixed answer model only the query and returned memory context.
2. Use a stable answer prompt requiring citations and abstention.
3. Grade deterministic fields where possible.
4. Use blinded LLM judges only where necessary.
5. Validate a sample against independent human/judge review.

### 8.4 Repetition

- Deterministic systems: one run plus environment reproducibility check.
- Model-based systems: at least three seeds/runs for the development report and more for final claims where variance is material.
- Report mean, standard deviation, and confidence intervals.

---

## 9. Anti-Gaming Rules

- No scenario IDs, gold answers, or query-specific special cases in prompts/code.
- Development and held-out scenario templates are separated.
- Paraphrases, names, dates, and distractors are procedurally varied in held-out generation.
- The benchmark runner hashes submitted configuration and outputs.
- Manual exclusions require public rationale.
- Answer prompts cannot contain category-specific hints unavailable in production.
- Judge prompts and raw judge results are published.
- Retrieval and answer scores are not conflated.
- Test fixtures containing known benchmark text are checked against package source where feasible.

---

## 10. Launch Gates

Provisional targets; adjust only before the held-out set is opened:

### Deterministic core

- 100% correct explicit current/historical state over golden fixtures.
- 0% cross-bank leakage.
- 0% irreversible mutation.
- 100% export/import authoritative-record round trip.

### Inferred dream system

- At least 95% evidence-span validity.
- False supersession below 1% on held-out state-change scenarios.
- Unauthorized directive creation 0%.
- Cross-bank leakage 0%.
- Every active derived claim reaches at least one valid source span.
- Material improvement over hybrid-retrieval-only baseline on current and historical state accuracy.

### Performance

- Structured local recall p95 under 100 ms at 100,000 claims, excluding model reranking.
- Explicit local observation writes p95 under 50 ms, excluding optional embedding.
- Dream runs resume correctly after forced interruption.

These thresholds are engineering gates, not prewritten marketing claims.

---

## 11. Repository Layout

```text
benchmarks/memoryrotbench/
├── README.md
├── schema/
│   ├── scenario.schema.json
│   └── result.schema.json
├── scenarios/
│   ├── public/
│   ├── development/
│   └── heldout-manifest.json
├── generators/
├── adapters/
│   ├── base.py
│   ├── flat_context.py
│   ├── fts.py
│   ├── vector.py
│   ├── memorygraph.py
│   └── external/
├── graders/
├── runner/
├── analysis/
├── configs/
└── reports/
```

One command should reproduce a published report:

```bash
uv run memoryrotbench run --config benchmarks/configs/launch.yaml
uv run memoryrotbench report RUN_ID
```

---

## 12. Required Published Report

The launch report includes:

- System/version table.
- Hardware and environment.
- Dataset/category counts.
- Exact configurations.
- Construction, retrieval, maintenance, security, and efficiency tables.
- Per-category error analysis.
- At least ten representative failures from MemoryGraph.
- Ablations:
  - Without vectors.
  - Without graph expansion.
  - Without dream resolution.
  - Without challenge.
  - Without predicate cardinality.
- Raw output artifact links.
- Reproduction instructions.

Publishing honest negative results is preferable to an inflated aggregate. Trustworthy evaluation reinforces the product’s provenance positioning.
