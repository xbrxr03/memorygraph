# MemoryGraph Intelligence Sweep

**Status:** Architecture input  
**Date:** August 21, 2026  
**Purpose:** Source-backed patterns to adopt, adapt, or avoid before implementation  
**Supersedes:** Unsupported conclusions in files `01` through `06`; those files remain useful discovery notes, not implementation authority.

---

## 1. Executive Findings

The strongest agent-memory systems have converged on several ideas:

1. **Raw episodes must survive extraction.** Derived facts are fallible; the original observation is the audit trail and recovery mechanism.
2. **Useful retrieval is hybrid.** BM25/full-text, dense similarity, graph neighborhood, metadata, and time each recover different relevant items.
3. **Memory ingestion is an asynchronous pipeline.** Extraction and entity resolution are too expensive and uncertain for an atomic hot-path write.
4. **Tenant and scope isolation must be structural.** Filtering by user, agent, project, or session cannot be left to prompts.
5. **Derived summaries are caches.** They should be traceable to source claims and safe to rebuild.
6. **Semantic models propose; deterministic code commits.** LLM output must pass schema, provenance, temporal, authorization, and conflict-policy validation before it changes current belief state.
7. **Observability is a product feature.** Users need to see what a background job processed, which evidence caused a revision, and how to reverse it.
8. **Memory security is a first-class requirement.** Persistent stores create a delayed prompt-injection and poisoning surface.
9. **Chat recall benchmarks are insufficient.** The most useful target is whether an agent learns environment state, workflows, gotchas, and invalid premises over repeated work.

The opportunity is therefore not “another vector store” or “a graph that remembers.” It is a **revision and evidence layer for agent beliefs**.

---

## 2. Patterns Worth Adopting

### 2.1 Graphiti: episodes, temporal edges, and hybrid retrieval

Graphiti keeps episodic source nodes and derives entity edges with `valid_at`, `invalid_at`, `created_at`, and `expired_at`. Its resolution pipeline can return resolved, invalidated, and new edges. It also links entity edges to the episodes from which they were extracted.

This confirms two important choices:

- World-valid time and system-record time are different dimensions.
- Contradiction resolution is a semantic operation, while temporal consistency checks and commits can remain deterministic.

Graphiti does not rely on graph traversal alone. Its published search recipes combine BM25, cosine similarity, BFS, reciprocal-rank fusion, MMR, and cross-encoder reranking across edges, nodes, episodes, and communities.

**Adopt:**

- Immutable source episodes.
- Valid-time and system-time fields.
- Claim invalidation without history deletion.
- Hybrid candidate generation and explicit reranking.
- Evidence links from claims to source episodes.

**Change:**

- Use embedded SQLite for the local product instead of requiring a graph server.
- Treat model-produced timestamps and contradictions as proposals, not immediately trusted mutations.
- Store exact evidence spans and extractor versions, not only episode IDs.

Sources: [Graphiti edge operations](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/edge_operations.py), [Graphiti edge schema](https://github.com/getzep/graphiti/blob/main/graphiti_core/edges.py), [Graphiti search recipes](https://github.com/getzep/graphiti/blob/main/graphiti_core/search/search_config_recipes.py).

### 2.2 Mem0: minimal API, raw/inferred modes, and scopes

Mem0’s product surface is successful because the integration loop is small: retrieve, enrich, generate, store. Its documented ingestion supports inferred and raw storage modes, asynchronous processing, stable identifiers, and user/agent/app/session scopes.

**Adopt:**

- A two-verb beginner API: `remember` and `recall`.
- `infer=true|false` rather than forcing extraction on every write.
- Asynchronous inferred ingestion with operation IDs.
- Explicit bank/user/agent/project/session isolation.
- Idempotency keys for imports and repeated session capture.

**Avoid:**

- Treating replacement text as sufficient history.
- Mixing raw and inferred ingestion without idempotency and provenance controls.
- Allowing memories to accumulate without a revision policy.

Source: [Mem0 platform architecture](https://github.com/mem0ai/mem0/blob/main/skills/mem0/references/architecture.md).

### 2.3 Letta: versioned, inspectable context

Letta’s context repositories treat agent context as local files synchronized through Git. The important pattern is not Git itself; it is that memory edits become inspectable, attributable, reversible versions.

**Adopt:**

- Human-readable exports.
- A complete mutation event log.
- Diff, history, explanation, and rollback commands.
- Portable agent/bank export.

**Change:**

- Keep the database event log authoritative.
- Make Git snapshots an optional adapter so core correctness does not depend on filesystem merges or Git availability.

Source: [Letta context repositories](https://www.letta.com/blog/context-repositories/).

### 2.4 Cognee: replaceable pipeline stages

Cognee separates data units, tasks, and pipelines across relational, vector, and graph responsibilities. This is a sound extensibility pattern even though three independent databases would violate MemoryGraph’s zero-infrastructure wedge.

**Adopt:**

- Typed pipeline inputs and outputs.
- Replaceable extraction, embedding, reranking, and verification providers.
- Session memory distinct from durable memory.
- Local defaults with production backend seams.

**Avoid:**

- Requiring relational, graph, and vector servers for the first useful experience.

Source: [Cognee architecture overview](https://docs.cognee.ai/core-concepts/overview).

### 2.5 Hindsight: banks, operational discipline, and cached mental models

Hindsight’s documented practices expose several details that are easy to overlook:

- Stable `document_id` values prevent duplicate ingestion.
- Retain and recall should not be assumed to complete within the same turn.
- Tags provide strict memory isolation.
- Metadata is returned for traceability.
- Recall returns evidence; reflect performs slower synthesis.
- “Mental models” are precomputed answers for common, slowly changing queries.
- Async operations expose the job payload and affected document IDs.
- Its retain pipeline moved semantic pre-resolution outside the write transaction to reduce database lock contention.

**Adopt:**

- Stable source IDs and content hashes.
- Separate evidence retrieval from answer synthesis.
- Materialized profiles/summaries as traceable caches.
- Durable job state, payload visibility, retries, and affected-resource lists.
- Read-heavy pre-resolution outside short atomic write transactions.

**Avoid:**

- Treating arbitrary metadata as if it automatically affects retrieval.
- Hidden background work whose completion semantics are unclear.

Sources: [Hindsight best practices](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/src/pages/best-practices.mdx), [retain pipeline changes](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/blog/2026-04-07-version-0-5-0.md), [async observability and consolidation](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/blog/2026-04-15-version-0-5-2.md).

### 2.6 Graphify: staged architecture and distribution

Graphify’s architecture uses a sequence of small stages with plain data contracts. Its adoption surface is equally important: one install command, an agent skill, a reproducible worked example, platform adapters, persistent output, and benchmark commands.

**Adopt:**

- One responsibility per pipeline stage.
- Typed plain-data contracts between stages.
- A deterministic base mode that works without a model.
- A tiny worked example checked into the repository.
- Agent Skills plus MCP and platform-specific installers.
- One-command reproducible benchmarks.

**Avoid:**

- Absolute paths in persistent identities or cache keys.
- Nonportable modification-time-based invalidation.
- Generated artifacts that cause unavoidable merge churn.
- Hooks that block normal agent operation when the memory service is unhealthy.

Sources: [Graphify architecture](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md), [reproducible example](https://github.com/Graphify-Labs/graphify/blob/v8/worked/example/README.md), [installation surface](https://github.com/Graphify-Labs/graphify), [team portability issue](https://github.com/Graphify-Labs/graphify/issues/369).

### 2.7 SQLite: local-first with explicit extension boundaries

SQLite supplies transactions, FTS5, recursive CTEs, JSON functions, and WAL mode in one file. `sqlite-vec` adds local vector search but remains pre-1.0 and warns that breaking changes should be expected.

**Adopt:**

- SQLite as the authoritative local store.
- WAL mode, bounded transactions, foreign keys, and migrations.
- FTS5 in the base installation.
- A vector-provider interface with `sqlite-vec` as an optional local implementation.
- Embedding model/version recorded beside every vector.

**Avoid:**

- Making a pre-1.0 extension part of the persistence contract.
- Treating a vector index as authoritative state.

Sources: [SQLite WAL](https://www.sqlite.org/wal.html), [sqlite-vec repository](https://github.com/asg017/sqlite-vec).

---

## 3. Benchmark Intel

### 3.1 LongMemEval

The original LongMemEval contains 500 questions covering information extraction, multi-session reasoning, knowledge updates, temporal reasoning, and abstention. It is useful, but it primarily evaluates conversational question answering.

Source: [LongMemEval repository](https://github.com/xiaowu0162/longmemeval).

### 3.2 LongMemEval-V2

LongMemEval-V2 is better aligned with the proposed wedge. It evaluates memory over agent trajectories, including:

- Static environment state.
- Dynamic state tracking.
- Workflow knowledge.
- Environment-specific gotchas.
- Premise awareness.

It evaluates an accuracy-latency frontier, which discourages systems from buying quality through unlimited retrieval and reasoning.

**Decision:** LongMemEval-V2 becomes the primary external benchmark target after the custom rot benchmark.

Source: [LongMemEval-V2 repository](https://github.com/xiaowu0162/LongMemEval-V2/).

### 3.3 LoCoMo and BEAM

LoCoMo provides long-term conversational QA and summarization across ten released conversations. BEAM expands to 100 conversations and 2,000 questions at context lengths up to 10 million tokens, covering general, coding, and mathematical domains.

Use both as secondary evaluations. Do not market one self-reported aggregate as definitive proof of memory quality.

Sources: [LoCoMo repository](https://github.com/snap-research/locomo), [BEAM repository](https://github.com/mohammadtavakoli78/BEAM).

### 3.4 Benchmark policy

Every published number must include:

- Exact repository commit.
- Dataset version and exclusions.
- Ingestion, retrieval, answer, and judge models.
- Prompts and configuration.
- Token, latency, and monetary budgets.
- Seeds and retry behavior.
- Per-category results.
- Raw predictions and judge outputs.
- Confidence intervals where appropriate.
- A command that reproduces the table.

MemoryGraph will distinguish **retrieval performance**, **answer performance**, and **belief-maintenance performance**. Combining them into one number hides failure modes.

---

## 4. Security Intel

MINJA demonstrates that attackers can poison persistent agent memory through ordinary interaction, causing malicious records to be retrieved later. MemoryGraft extends this risk to poisoned successful experiences. These are structural threats: stored text can influence future reasoning long after the originating session.

Required consequences:

1. Raw memory content is untrusted data, never executable instruction.
2. Instructions/directives require a separate privileged API and table.
3. Retrieval renders quoted evidence with source boundaries.
4. Source actor, authorization scope, and trust class are mandatory.
5. Model-derived claims never inherit higher authority than their evidence.
6. Cross-bank retrieval and mutation are denied at the SQL repository layer.
7. High-impact procedural memories require stronger admission policy.
8. Deletion, quarantine, and complete provenance tracing are core operations.
9. Security evaluation includes direct and indirect memory poisoning.

Sources: [MINJA paper](https://arxiv.org/abs/2503.03704), [MemoryGraft paper](https://arxiv.org/abs/2512.16962).

---

## 5. Final Architecture Conclusions

The sweep resolves the original open questions as follows:

| Question | Decision |
|---|---|
| Graph or vector? | Relational temporal claim graph as truth; FTS and vectors as retrieval indexes. |
| Deterministic or LLM-powered? | Deterministic explicit mode plus optional semantic pipeline. Models propose; policy code commits. |
| One confidence score? | No. Separate evidence strength, currentness, importance, and retrieval relevance. |
| Nightly full dream? | No. Incremental durable jobs triggered by new observations, conflicts, thresholds, idle time, or manual request. |
| Summaries as memory? | No. Summaries are regenerable, citation-backed materialized views. |
| Automatic contradiction resolution? | Only for policy-safe cases; otherwise preserve the conflict and request review. |
| Auto-expiry? | Only for explicitly volatile claim classes or declared validity windows. No generic truth decay. |
| Local storage? | SQLite with FTS5; optional vector extension behind an interface. |
| First integration? | Agent Skills + MCP core, with Claude Code/Codex/OpenCode adapters layered on top. |
| Primary wedge? | Long-running coding agents learning project state, workflows, decisions, and gotchas. |
| Primary proof? | Reproducible MemoryRotBench plus LongMemEval-V2. |

The detailed contracts implementing these decisions are defined in specifications `08` through `11`.
