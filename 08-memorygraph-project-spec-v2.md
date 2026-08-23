# MemoryGraph Project Specification v2

**Status:** Approved implementation contract; research amendments frozen  
**Date:** August 21, 2026  
**Audience:** Founder, maintainers, contributors, integration authors  
**Authority:** This document supersedes `06-memorygraph-v1-spec.md` for product scope and architecture.  
**Working name:** MemoryGraph; final package and company naming remain separate decisions.

---

## 1. Product Definition

### 1.1 One-line definition

MemoryGraph is a local-first revision and evidence layer for agent beliefs: it records what an agent observed, derives versioned claims, tracks when those claims were valid, and explains why a current claim should be trusted.

### 1.2 User-facing promise

> Memory that can show its work.

“Memory that doesn’t rot” remains the narrative, but it is not an absolute technical guarantee. The testable product promises are:

- Preserve original evidence.
- Distinguish current truth from historical truth.
- Detect and retain conflicts.
- Never silently destroy history.
- Bound the context returned to agents.
- Explain every derived claim and revision.
- Make semantic mutations reversible.

### 1.3 Initial user

The first user is a developer running a coding agent against the same projects for weeks or months. Their recurring failures include:

- Relearning build commands and environment gotchas.
- Following decisions that were later reversed.
- Reintroducing previously fixed errors.
- Treating an old migration or dependency state as current.
- Losing the evidence behind architectural conventions.
- Loading a large context file containing irrelevant or stale instructions.

### 1.4 Jobs to be done

1. “Remember this observation with a source.”
2. “Give my agent the few relevant current claims for this task.”
3. “Tell me what was believed at a prior point in time.”
4. “Show why this memory is considered current.”
5. “Tell me what conflicts or uncertain changes need review.”
6. “Move memory between tools without losing provenance.”
7. “Undo a bad automatic maintenance decision.”

### 1.5 Product wedge and expansion

The wedge is project memory for coding agents. The core data model remains domain-neutral so it can later support personal assistants, support agents, and enterprise workflow agents.

Expansion does not justify premature features. Cloud sync, team collaboration, and hosted retrieval are outside the first implementation but must not require a data-model rewrite.

---

## 2. Product Principles

1. **Evidence before inference.** Store the observation before derived claims.
2. **Append history; materialize current state.** Historical truth is authoritative; current views are derived.
3. **Models propose; policy commits.** LLMs never issue direct SQL mutations.
4. **Relevance is not truth.** Retrieval score and belief strength remain separate.
5. **Uncertainty is a valid result.** Contested claims must not be forced into a false winner.
6. **Local-first is the default.** The useful core runs from one SQLite file.
7. **Semantic capabilities are replaceable.** Embedding and LLM providers are adapters.
8. **Background work is observable.** Every job has durable state, inputs, outputs, cost, and errors.
9. **All content is untrusted.** Stored text never becomes privileged instruction implicitly.
10. **Benchmarks are executable artifacts.** Marketing claims must trace to reproducible runs.

---

## 3. Scope

### 3.1 v0.1: deterministic temporal core

- SQLite database and migrations.
- Memory banks and strict scoping.
- Immutable observations and source spans.
- Entities, aliases, predicates, temporal claims, evidence, and claim relations.
- Explicit claim creation, confirmation, contradiction, supersession, and retraction.
- Current and historical structured queries.
- FTS5 retrieval.
- Explanation and history APIs.
- JSONL/JSON/Markdown export.
- CLI.
- Full audit event log.
- Temporal and isolation property tests.

### 3.2 v0.2: inferred ingestion and dream cycle

- Provider interfaces for extraction, embedding, reranking, and challenge.
- Structured-output claim extraction.
- Entity resolution.
- Hybrid FTS/vector/graph candidate generation.
- Dream proposals, validation, atomic commit, dry-run, review queue, and rollback.
- Optional local embedding extra.
- Derived, citation-backed materialized views.

### 3.3 v0.3: agent integration

- MCP server.
- Agent Skill package.
- Claude Code, Codex, and OpenCode installers/adapters.
- Automatic bounded recall and end-of-turn/session observation capture where supported.
- Flat-file and common memory-format importers.
- Integration health checks and fail-open behavior.

### 3.4 v1.0: launch contract

- Stable schema and documented migration policy.
- Stable Python API, CLI, MCP tool schema, and portable export format.
- MemoryRotBench public harness.
- LongMemEval-V2 integration.
- Security threat model and poisoning suite.
- Reproducible performance/cost report.
- Package signing/provenance and release automation.
- At least two production design partners or five sustained weekly users.

### 3.5 Explicit non-goals for v1

- Hosted multi-tenant cloud.
- Multi-writer database synchronization.
- Distributed graph database.
- Autonomous web verification of every fact.
- General ontology induction.
- Arbitrary-depth graph reasoning.
- A consumer-facing chat application.
- Automatic storage of every message by default.
- Silent deletion of low-scoring memories.
- Treating agent identity/persona instructions as ordinary facts.

---

## 4. Domain Model

### 4.1 Bank

A bank is the hard isolation boundary for one user, project, agent, or application context.

```text
Bank
- id: UUID
- slug: stable user-facing identifier
- name
- mission: optional extraction guidance
- policy_json
- created_at
- archived_at
```

Every persisted resource except global schema metadata carries `bank_id`. Repository methods require it. Cross-bank joins are forbidden outside explicit administrative export tooling.

### 4.2 Observation

An observation is immutable source material.

```text
Observation
- id: UUID
- bank_id
- kind: message | tool_result | file | document | event | explicit_assertion | import
- source_key: stable external identifier
- content_sha256
- content
- actor_type: user | agent | tool | system | external
- actor_id
- observed_at: when MemoryGraph received it
- effective_at: when the described source/event applies, if known
- trust_class
- sensitivity
- metadata_json
- ingestion_state
- created_at
```

Idempotency is enforced on `(bank_id, source_key, content_sha256)`. Reusing a source key with different content creates a new observation version; it does not overwrite the old source.

Long observations may have immutable chunks:

```text
ObservationChunk
- id
- observation_id
- ordinal
- start_offset
- end_offset
- content
- content_sha256
```

Offsets refer to Unicode code points in the normalized stored content. Normalization must not change the stored raw payload; a separate normalized representation may be indexed.

### 4.3 Entity

An entity is a canonical subject or object.

```text
Entity
- id
- bank_id
- canonical_name
- entity_type
- description
- status: active | merged | archived
- merged_into_id
- created_at
```

Aliases are evidence-backed:

```text
EntityAlias
- id
- bank_id
- entity_id
- alias
- normalized_alias
- source_observation_id
- confidence
- created_at
```

Entity merges are reversible events. Persistent identity never depends on an absolute filesystem path. Project files use repository-relative POSIX paths plus repository identity.

### 4.4 Predicate definition

Predicates carry resolution policy.

```text
PredicateDefinition
- bank_id nullable for built-ins
- name
- subject_type nullable
- object_type nullable
- cardinality: one | many | event
- volatility: immutable | durable | volatile | ephemeral
- conflict_policy
- default_validity_seconds nullable
- sensitivity
- created_at
```

Examples:

| Predicate | Cardinality | Volatility | Resolution behavior |
|---|---|---|---|
| `works_at` | one | volatile | Overlapping distinct objects are conflict candidates. |
| `prefers` | many | volatile | New objects do not automatically replace old ones. |
| `uses_dependency_version` | one | volatile | Newer project/tool evidence may supersede. |
| `decided` | event | immutable | Later changes create new decision events. |
| `caused_failure` | many | durable | Multiple causes may coexist. |

Unknown predicates default to `many` and cannot auto-supersede. This conservative default prevents false conflict resolution.

### 4.5 Claim

A claim is an atomic proposition, not a prose memory blob.

```text
Claim
- id: UUID
- bank_id
- subject_entity_id
- predicate
- object_kind: entity | string | number | boolean | datetime | json
- object_entity_id nullable
- object_value_json nullable
- polarity: positive | negative
- valid_from nullable
- valid_to nullable
- system_from
- system_to nullable
- lifecycle: active | contested | retracted | superseded
- origin: explicit | extracted | imported | derived
- importance: 0..1
- created_by_run_id nullable
- created_at
```

Bi-temporal meaning:

- `[valid_from, valid_to)` is when the claim is believed to hold in the described world.
- `[system_from, system_to)` is when this version existed in MemoryGraph’s belief state.

Unknown bounds are `NULL`; they do not mean infinity semantically. Query functions define explicit handling for unknown time.

Each row is one immutable claim version whose `lifecycle` applies during its system-time interval. `system_to` is the only permitted in-place change. Any lifecycle, valid-time, importance, subject, predicate, object, or polarity change closes the old row and inserts a successor row with a new ID in the same transaction. Dream proposals remain in proposal tables and therefore do not require a `proposed` claim lifecycle.

`currentness` is computed at query/review time from predicate volatility, supporting evidence, explicit validity, and the query timestamp. It is returned with recall results but is not persisted as authoritative claim state.

### 4.6 Evidence

```text
ClaimEvidence
- id
- bank_id
- claim_id
- observation_id
- chunk_id nullable
- start_offset
- end_offset
- excerpt
- stance: supports | contradicts | mentions
- explicitness: explicit | strongly_implied | inferred
- source_reliability: 0..1
- extraction_confidence: 0..1
- extractor_name
- extractor_version
- created_at
```

Validation requires the stored excerpt to match the referenced source span after the documented normalization procedure. A model may propose a span; deterministic code verifies it.

### 4.7 Claim relationship

```text
ClaimRelation
- id
- bank_id
- from_claim_id
- to_claim_id
- relation: supersedes | contradicts | refines | duplicates | derived_from
- rationale
- decision_method: explicit | rule | model_proposal | human_review
- decision_confidence
- dream_run_id nullable
- created_at
```

Relationships do not erase either claim.

### 4.8 Directive

Behavioral instructions are separated from memory claims.

```text
Directive
- id
- bank_id
- scope
- text
- authority: owner | administrator | application
- source_observation_id nullable
- enabled
- valid_from
- valid_to
- created_at
```

Only privileged APIs may create directives. Inferred ingestion can never create them.

### 4.9 Materialized artifact

Profiles, summaries, and runbooks are regenerable caches.

```text
Artifact
- id
- bank_id
- kind: profile | topic_summary | runbook | export
- key
- content
- source_claim_ids_json
- source_watermark
- generator_name
- generator_version
- stale_at nullable
- created_at
```

Artifacts never become evidence for their own source claims. This prevents summary-of-summary feedback loops.

### 4.10 Event log

```text
MemoryEvent
- sequence: monotonically increasing integer
- event_id: UUID
- bank_id
- event_type
- aggregate_type
- aggregate_id
- actor_type
- actor_id
- payload_json
- idempotency_key nullable
- created_at
```

The event log supports audit and logical rollback. It is not initially the sole storage engine; normalized tables remain the operational read model.

---

## 5. Truth and Revision Semantics

### 5.1 Relevance and belief are separate

MemoryGraph tracks independent values:

- **Source reliability:** authority of the evidence source.
- **Extraction confidence:** confidence that the claim matches the source.
- **Currentness:** estimated likelihood that a volatile claim remains current.
- **Importance:** value of retaining/retrieving the claim.
- **Retrieval relevance:** query-specific score computed at recall time.

No formula collapses these into a permanent universal confidence score.

### 5.2 Evidence authority defaults

Default precedence, configurable per bank:

1. Current explicit owner/user correction.
2. Current authoritative tool result.
3. Current authoritative repository/file state for project claims.
4. Current authoritative external source.
5. Repeated direct observations.
6. Single explicit historical statement.
7. Strong implication.
8. Model inference.
9. Unattributed imported text.

Recency only breaks ties where the predicate is mutable and sources have comparable authority.

### 5.3 Conflict definition

Claims are automatic conflict candidates only when all are true:

- Same bank.
- Same canonical subject.
- Same predicate or declared inverse/exclusive predicate.
- Predicate cardinality is `one`, or one claim is explicit negation of the other.
- Valid intervals overlap or current validity is unknown.
- Objects/polarity differ materially.

For `many` predicates, distinct objects coexist unless explicit negation or an exclusive constraint says otherwise.

### 5.4 Automatic resolution eligibility

A dream proposal may auto-commit supersession only when:

- Predicate policy permits it.
- New evidence is explicit or authoritative.
- Evidence span validation passes.
- Temporal order is coherent.
- No higher-authority contrary evidence exists.
- Decision score exceeds the configured threshold.
- The action does not alter a directive or protected claim.

Otherwise both claims remain available with lifecycle `contested`, and a review item is created.

### 5.5 Currentness policy

Currentness changes only for predicates declared `volatile` or `ephemeral`.

- `immutable`: no automatic currentness decay.
- `durable`: no automatic decay; may be challenged after contradictory evidence.
- `volatile`: currentness may decay toward a policy floor based on time since supporting observation.
- `ephemeral`: requires a validity window or default TTL.

Low currentness affects retrieval and review priority; it does not delete or falsify a claim.

### 5.6 As-of queries

Every claim query may specify:

- `valid_at=T`: what was true in the described world at T.
- `known_at=T`: what MemoryGraph believed at T.
- Both: what the system knew at T about world state at another time.

Current queries use `known_at=now` and `valid_at=now`, while explicitly handling unknown validity. Historical queries never consult only the latest materialized state.

---

## 6. Storage Architecture

### 6.1 Authoritative store

SQLite is authoritative for v1.

Required connection settings:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

The storage layer owns connections and transactions. Domain services do not execute handwritten SQL outside repository modules.

### 6.2 Indexes

Required relational indexes include:

- Every table’s `bank_id` leading index.
- Observation `(bank_id, source_key, content_sha256)` unique index.
- Claims by `(bank_id, subject_entity_id, predicate, system_to)`.
- Claims by valid and system intervals.
- Evidence by claim and observation.
- Aliases by normalized alias.
- Jobs by state and lease.
- Events by bank and sequence.

FTS5 indexes:

- Observation chunks.
- Entity names and aliases.
- Claim canonical text.
- Materialized artifacts.

### 6.3 Vector index

Vectors are disposable indexes:

```text
Embedding
- bank_id
- resource_type
- resource_id
- model
- dimensions
- content_sha256
- vector
- created_at
```

The provider contract supports:

- In-process exact search for small test stores.
- Optional `sqlite-vec` local implementation.
- Future remote implementations.

If the vector extension fails to load, FTS and structured retrieval remain functional. A database never becomes unreadable solely because an optional index provider is absent.

### 6.4 Concurrency model

- One short writer transaction at a time.
- Extraction, embeddings, entity candidate search, and model calls occur outside the writer transaction.
- Commit revalidates proposal preconditions against the latest event watermark.
- Stale proposals are retried or moved to review.
- Read connections may run concurrently under WAL.

### 6.5 Migrations

- Ordered SQL migration files checked into the package.
- `schema_version` and `minimum_reader_version` recorded.
- Migration runs under backup and transaction where SQLite permits.
- Destructive migrations require an explicit export/backup step.
- The portable export format has its own independent version.

### 6.6 Portability

Canonical export is versioned JSONL:

```text
manifest
banks
observations
entities
aliases
predicates
claims
evidence
claim_relations
directives
events optional
```

Exports use relative source identifiers where possible and never include provider secrets. Markdown is a presentation format, not lossless interchange.

---

## 7. Retrieval Architecture

### 7.1 Recall modes

```text
evidence  -> ranked claims plus citations; no generated answer
context   -> token-budgeted agent context block
answer    -> optional synthesis over evidence
history   -> timeline without current-state suppression
```

The default SDK `recall` mode is `evidence`.

### 7.2 Query plan

1. Validate bank and caller scope.
2. Parse explicit structured filters.
3. Optionally use a query planner to identify entities, predicates, and temporal intent.
4. Generate candidates independently from:
   - FTS5/BM25.
   - Vector similarity if configured.
   - Exact entity/alias matches.
   - One-hop graph neighborhood.
   - Recent/project-active claims.
5. Apply bank, lifecycle, sensitivity, and as-of filters.
6. Fuse rankings using reciprocal-rank fusion.
7. Optionally rerank the bounded candidate set.
8. Diversify repeated/near-identical claims.
9. Attach the strongest evidence spans.
10. Fit the caller’s claim and token budgets.

### 7.3 Query planner

Natural-language recall is permitted in v0.2 through an optional structured-output planner. The planner may propose filters; it cannot expand authorization scope.

When no planner is configured, recall uses keywords plus any explicit SDK filters. The API never implies full natural-language graph understanding in deterministic mode.

### 7.4 Ranking

Candidate relevance may include:

- RRF fused rank.
- Exact entity/predicate match.
- Temporal fit.
- Currentness.
- Importance.
- Evidence strength.
- Graph distance.
- Session/project affinity.

Truth state is a filter/annotation, not merely another similarity feature. A semantically close superseded claim cannot outrank its active successor for a current query.

### 7.5 Output contract

Each recalled item includes:

```json
{
  "claim_id": "...",
  "text": "Abrar works on MemoryGraph",
  "lifecycle": "active",
  "valid_from": "...",
  "valid_to": null,
  "relevance": 0.84,
  "currentness": 0.93,
  "evidence": [
    {
      "observation_id": "...",
      "source_key": "conversation:thread-123:turn-8",
      "excerpt": "...",
      "observed_at": "..."
    }
  ],
  "warnings": []
}
```

Contested items include competing claim IDs and must never be rendered as unqualified fact.

### 7.6 Context budget

Default context export limits:

- Maximum 8 claims.
- Maximum 1,200 tokens.
- Maximum 2 claims from one duplicate cluster.
- At least one citation per claim.
- Directives rendered separately from evidence.

Budgets are configurable per adapter. Full stores are never injected automatically.

---

## 8. Public Interfaces

### 8.1 Python API

```python
from memorygraph import MemoryGraph

memory = MemoryGraph.open("./.memorygraph/memory.db")

observation = memory.observe(
    bank="project:memorygraph",
    content="We switched the package build backend to hatchling.",
    source_key="codex:thread-123:turn-45",
    kind="explicit_assertion",
    actor={"type": "user", "id": "abrar"},
)

claim = memory.assert_claim(
    bank="project:memorygraph",
    subject="MemoryGraph",
    predicate="uses_build_backend",
    object="hatchling",
    evidence={"observation_id": observation.id, "excerpt": "..."},
)

result = memory.recall(
    bank="project:memorygraph",
    query="How is the package built?",
    mode="evidence",
    limit=8,
)

explanation = memory.explain(bank="project:memorygraph", claim_id=claim.id)
timeline = memory.history(
    bank="project:memorygraph",
    subject="MemoryGraph",
    predicate="uses_build_backend",
)

run = memory.dream(bank="project:memorygraph", dry_run=True)
```

Beginner API:

```python
memory.remember(text, bank=..., source_key=..., infer=True)
memory.recall(query, bank=...)
```

Advanced explicit methods:

```text
observe
assert_claim
confirm
contradict
supersede
retract
recall
explain
history
dream
review
rollback
export
import_data
```

### 8.2 CLI

```bash
memorygraph init
memorygraph bank create project:memorygraph
memorygraph observe --bank project:memorygraph --source-key ... "..."
memorygraph remember --bank project:memorygraph --infer "..."
memorygraph recall --bank project:memorygraph "How is the package built?"
memorygraph explain --bank project:memorygraph CLAIM_ID
memorygraph history --bank project:memorygraph --subject MemoryGraph --predicate uses_build_backend
memorygraph dream --bank project:memorygraph --dry-run
memorygraph review list --bank project:memorygraph
memorygraph review approve PROPOSAL_ID
memorygraph rollback RUN_ID
memorygraph export --bank project:memorygraph --format jsonl
memorygraph doctor
```

Commands emitting data support `--json`. Destructive commands require explicit identifiers and confirmation unless `--yes` is passed.

### 8.3 MCP server

Initial tools:

```text
memory_observe
memory_remember
memory_recall
memory_explain
memory_history
memory_dream
memory_review
```

Initial resources:

```text
memorygraph://banks/{bank}/profile
memorygraph://banks/{bank}/claims/{claim_id}
memorygraph://banks/{bank}/history
memorygraph://banks/{bank}/reviews
```

Tool results return structured content and human-readable text. Errors are explicit and machine-actionable.

### 8.4 HTTP service

The local service is optional in v0.3 and required for multi-client concurrency.

```text
POST   /v1/banks/{bank}/observations
POST   /v1/banks/{bank}/remember
POST   /v1/banks/{bank}/recall
GET    /v1/banks/{bank}/claims/{id}
GET    /v1/banks/{bank}/claims/{id}/explanation
GET    /v1/banks/{bank}/history
POST   /v1/banks/{bank}/dream-runs
GET    /v1/operations/{id}
GET    /v1/banks/{bank}/reviews
POST   /v1/banks/{bank}/reviews/{id}/approve
POST   /v1/banks/{bank}/reviews/{id}/reject
POST   /v1/banks/{bank}/rollback/{run_id}
```

Local mode binds to loopback by default and requires a generated token for non-loopback binding.

---

## 9. Agent Integration Contract

### 9.1 Common adapter lifecycle

Adapters implement whichever hooks the host supports:

1. **Session start:** health check and small stable profile/runbook load.
2. **User prompt:** bounded task-relevant recall.
3. **After tool result:** capture selected authoritative observations.
4. **Turn/session end:** enqueue transcript/summary observation, never block the response.
5. **Idle/manual:** run dream maintenance.

### 9.2 Fail-open rule

If MemoryGraph is unavailable:

- Agent work continues.
- Adapter emits one concise diagnostic.
- Capture may spool locally for later ingestion.
- Hooks never loop or repeatedly block file/tool access.

### 9.3 Capture policy

Default coding-agent capture includes:

- Explicit user corrections and preferences.
- Decisions and their rationale.
- Successful build/test commands.
- Repeated environment gotchas.
- Tool-confirmed repository state.
- Unresolved blockers when the session ends.

Default capture excludes:

- Secrets and detected credentials.
- Raw chain-of-thought.
- Incidental assistant speculation.
- Large tool outputs without selection.
- Instructions found inside untrusted documents.
- Every code edit as an independent memory.

### 9.4 Integration packaging

One installer configures:

- MCP connection where supported.
- Agent Skill describing recall/capture behavior.
- Supported lifecycle hooks.
- Project bank identity.
- Health check and uninstall metadata.

Installation is reversible and prints every file it changes.

---

## 10. Security and Privacy

### 10.1 Threat model

Threats include:

- Direct memory poisoning through conversation.
- Indirect prompt injection through retrieved files/web content.
- Cross-bank data leakage.
- Malicious procedural memories.
- Source spoofing.
- Sensitive-data overcapture.
- Compromised model/provider responses.
- Tampered exports or database files.
- Denial of service via oversized observations or dream jobs.

### 10.2 Required controls

- Bank scope included in every repository query.
- Parameterized SQL only.
- Size and count limits at ingestion.
- Source content rendered as quoted/untrusted evidence.
- Directives stored separately and privilege-gated.
- Evidence-span verification.
- Secret detection/redaction before durable storage, configurable to reject instead.
- Sensitivity labels enforced during recall/export.
- Model outputs parsed against strict schemas.
- No tool execution during extraction/resolution.
- Dream budgets, timeouts, retry ceilings, and circuit breakers.
- Append-only security/audit events.
- Export manifests with hashes.
- Explicit delete and quarantine flows.

### 10.3 Deletion semantics

Deleting an observation:

1. Tombstones or removes source content according to configured retention policy.
2. Invalidates its evidence links.
3. Enqueues recomputation for affected claims and artifacts.
4. Records the deletion event without preserving deleted sensitive content in the event payload.
5. Regenerates indexes.

Claims supported only by deleted evidence cannot remain silently verified.

### 10.4 Local data protection

- Database and configuration files are user-only by default.
- Provider secrets live in environment variables or OS keychain adapters, never the database/export.
- Optional SQLCipher-compatible storage is a post-v1 adapter unless design partners require it earlier.

---

## 11. Provider Interfaces

```python
class Extractor(Protocol):
    def extract(self, observation: Observation, policy: ExtractionPolicy) -> ExtractionResult: ...

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> EmbeddingBatch: ...

class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[Candidate]) -> list[RankedCandidate]: ...

class Resolver(Protocol):
    def classify(self, new_claim: ClaimProposal, candidates: list[Claim]) -> ResolutionResult: ...

class Challenger(Protocol):
    def challenge(self, claim: Claim, evidence: list[Evidence]) -> ChallengeResult: ...

class Synthesizer(Protocol):
    def synthesize(self, claims: list[Claim], target: ArtifactSpec) -> ArtifactProposal: ...
```

Requirements:

- Provider name, model, version, prompt version, latency, and usage are recorded.
- Results are serializable and replayable.
- Provider exceptions never leave partial domain mutations.
- Test fake providers are first-class.
- Base deterministic functionality has no provider requirement.

---

## 12. Repository Architecture

```text
memorygraph/
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── ARCHITECTURE.md
├── pyproject.toml
├── uv.lock
├── Makefile
├── src/memorygraph/
│   ├── __init__.py
│   ├── api.py
│   ├── config.py
│   ├── errors.py
│   ├── models/
│   │   ├── bank.py
│   │   ├── observation.py
│   │   ├── entity.py
│   │   ├── claim.py
│   │   ├── evidence.py
│   │   ├── event.py
│   │   └── operation.py
│   ├── storage/
│   │   ├── database.py
│   │   ├── migrations/
│   │   ├── repositories/
│   │   ├── fts.py
│   │   └── vectors.py
│   ├── domain/
│   │   ├── observations.py
│   │   ├── claims.py
│   │   ├── temporal.py
│   │   ├── resolution.py
│   │   ├── policies.py
│   │   └── explanation.py
│   ├── retrieval/
│   │   ├── planner.py
│   │   ├── candidates.py
│   │   ├── fusion.py
│   │   ├── rerank.py
│   │   └── budgets.py
│   ├── dream/
│   │   ├── scheduler.py
│   │   ├── selector.py
│   │   ├── extractor.py
│   │   ├── resolver.py
│   │   ├── validator.py
│   │   ├── committer.py
│   │   ├── challenger.py
│   │   ├── artifacts.py
│   │   └── rollback.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── deterministic.py
│   │   ├── llm/
│   │   ├── embeddings/
│   │   └── rerankers/
│   ├── security/
│   │   ├── authorization.py
│   │   ├── sanitization.py
│   │   ├── secrets.py
│   │   └── trust.py
│   ├── importers/
│   ├── exporters/
│   ├── integrations/
│   │   ├── mcp/
│   │   ├── skills/
│   │   ├── claude_code/
│   │   ├── codex/
│   │   └── opencode/
│   ├── service/
│   └── cli/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── property/
│   ├── security/
│   ├── migrations/
│   └── fixtures/
├── benchmarks/
│   ├── memoryrotbench/
│   ├── longmemeval_v2/
│   ├── locomo/
│   └── reports/
├── examples/
│   ├── employment_change/
│   ├── coding_agent/
│   └── poisoning_defense/
└── docs/
```

Dependency direction:

```text
models <- domain <- application/API <- CLI/MCP/HTTP
models <- storage repositories <- application/API
provider protocols <- dream/retrieval <- application/API
```

Domain modules do not import CLI, HTTP, agent adapters, or concrete model SDKs.

---

## 13. Engineering Standards

- Python 3.11+.
- `src/` package layout.
- Pydantic v2 for external schemas; immutable dataclasses are acceptable internally.
- Standard `sqlite3` or a thin explicit wrapper; no ORM in the temporal core.
- Typer for CLI.
- Ruff for lint/format, mypy or pyright strict for core modules.
- Pytest, Hypothesis, and golden fixtures.
- AnyIO/asyncio only at job, provider, service, and adapter boundaries; deterministic domain logic remains synchronous.
- Semantic versioning after v1; pre-v1 versioned migration notes.
- Conventional commits are recommended, not required for outside contributors.

No generated code or provider response is accepted without validation and tests at its boundary.

---

## 14. Quality Gates

### v0.1 exit

- 100% bank-isolation property tests.
- 100% expected results on deterministic current/historical fixtures.
- No destructive history overwrite API.
- Export/import round-trip preserves all authoritative records.
- `explain` reaches a source span for every non-imported active claim.
- Database recovers cleanly after simulated process interruption at transaction boundaries.

### v0.2 exit

- Dream runs are idempotent under repeated delivery.
- Every auto-committed semantic mutation has verified evidence and a reversible event set.
- Low-confidence/invalid proposals cause no domain mutation.
- Provider failure causes no partial commit.
- Poisoning fixtures cannot create directives or cross-bank claims.
- Hybrid retrieval beats the FTS-only baseline on the development set without regressing exact-match cases beyond the agreed tolerance.

### v1 exit

- Public benchmark command reproduces published tables.
- p95 local structured recall under 100 ms for 100,000 claims on reference hardware, excluding model reranking.
- p95 explicit observation write under 50 ms excluding optional embeddings.
- No critical security findings in the documented threat suite.
- Clean installation and uninstall on macOS, Linux, and Windows.
- Agent adapters fail open.
- Schema and export-format compatibility tests pass across supported versions.

---

## 15. Decisions Frozen for Implementation

The following are architecture decisions, not open design questions:

- Observations are immutable and precede inference.
- Claims are atomic subject-predicate-object propositions with bi-temporal intervals.
- SQLite is the authoritative v1 store.
- FTS5 is mandatory; vectors are optional indexes through a provider.
- Natural-language planning and semantic maintenance require an explicit provider.
- LLMs cannot directly mutate storage.
- Predicate cardinality controls conflict semantics.
- Generic confidence decay is removed.
- Artifacts/summaries are derived caches.
- Directives are privilege-separated from memories.
- Background maintenance is incremental, durable, inspectable, and reversible.
- MCP plus Agent Skills is the cross-agent integration foundation.
- MemoryRotBench is required before launch claims.

Changes to these decisions require an architecture decision record explaining the evidence, compatibility cost, migration, and benchmark impact.

---

## 16. Research Amendments Frozen for the MVP

These amendments were approved after the implementation-intelligence sweep. They refine the
contract above and take precedence where wording differs.

### 16.1 Product boundary

MemoryGraph is an **auditable memory control plane for agents**. The graph is the temporal
revision, provenance, and explanation engine; it is not the only retriever and it is not the
primary marketing claim. Raw observations and append-only memory events are authoritative.
Claims, graph relations, indexes, summaries, and Markdown pages are reproducible projections.

### 16.2 Retrieval boundary

Recall is hybrid and bounded. It may fuse lexical, embedding, entity, graph-neighborhood, and
freshness signals, but it must preserve exact lexical matches and expose why each item was
selected. Graph-only retrieval, unbounded traversal, and community detection are excluded from
the MVP. A retrieval-time security screen marks or suppresses instruction-like, quarantined, or
untrusted content before it reaches an agent.

### 16.3 Dream Cycle safety modes

Dream consolidation is never an unconditional writer. Every bank chooses one of four modes:

- `off`: no consolidation is scheduled.
- `shadow`: proposals and evaluation artifacts are produced, with no semantic commit.
- `review`: validated proposals wait for a human decision.
- `auto`: only policy-eligible, fully evidenced, reversible proposals may commit.

Raw episodes remain first-class after consolidation. Automatic TTL expiry, generic confidence
decay, evidence-free pruning, and destructive summarization are forbidden.

### 16.4 Procedural and experiment memory

The coding-agent wedge must preserve attempts as well as facts. The portable semantic model
includes `Attempt`, `Outcome`, `Strategy`, `Failure`, and `Applicability` records linked to raw
observations and project-relative resources. Failed attempts are retained because they prevent
repeated work. Applicability is explicit; a successful strategy in one environment is not
silently generalized to another.

### 16.5 Freshness and evidence dimensions

User-facing memories declare one freshness form:

- `timeless`: expected to remain valid unless contradicted.
- `snapshot`: a claim about a specific observed/effective time.
- `pointer`: a reference whose target must be re-read for current truth.

Derivation method, source authority, evidence strength, extraction confidence, and currentness
are separate dimensions. `extracted` means model-derived, not trusted. Currentness is computed
at query time and is never treated as a generic decaying confidence score.

### 16.6 Agent-facing surface

The initial MCP surface is exactly five tools: `recall`, `record`, `explain`, `correct`, and
`forget`. Bank/workspace scope is required on every call. Read-only and destructive annotations
must be accurate, and corrections/deletions must remain auditable. Host integrations fail open:
an unavailable MemoryGraph must not block the coding agent's primary work.

### 16.7 Human review surface

The MVP emits a deterministic, Obsidian-compatible Markdown projection with stable links,
frontmatter, provenance, current/history separation, and review queues. Obsidian is an optional
viewer, not an authoritative database and not a required dependency. A custom web dashboard is
deferred until dogfooding demonstrates a workflow that Markdown cannot support.

### 16.8 Evaluation contract

The evaluator, scenario corpus, and run log are immutable during one experiment. Every change
records its hypothesis, baseline, command, result, and keep/revert decision. Required baselines
are: no memory, bounded Markdown/runbook, FTS/BM25, hybrid retrieval, Graphify, graph without
Dream, gated Dream, and always-on Dream. Quality is measured on downstream task success as well
as retrieval. The deletion suite audits raw observations, chunks, evidence, claims, indexes,
embeddings, artifacts, exports, caches, and generated Markdown for residue.
