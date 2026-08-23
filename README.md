# MemoryGraph

> Your agent found a relevant memory. MemoryGraph tells it whether that memory is still true.

MemoryGraph is a local-first evidence and revision layer for AI-agent beliefs. It preserves
source observations, represents claims as a temporal graph, and explains why a claim is
current, historical, or contested.

## MVP status

The deterministic MVP is running. The authoritative architecture starts at
[`00-architecture-index.md`](00-architecture-index.md); the implemented kernel covers:

- Immutable source observations.
- Typed entity nodes and atomic claim edges with exact evidence spans.
- Bi-temporal current and historical belief queries.
- Explicit confirmation, contradiction, and atomic supersession.
- `recall`, `history`, and `explain` commands.
- Hard bank isolation and an append-only mutation event log.
- A provider-agnostic dream-proposal validator with evidence, watermark, claim-version,
  idempotency, confidence, challenger, and review gates.
- Durable dream runs, tasks, proposals, review items, leases, event watermarks, and atomic
  proposal commits.
- A deterministic metadata provider that exercises the complete dream cycle without sending
  source data to an external model.
- MemoryRotBench fixtures, baselines, retrieval grading, and engine integration.
- Hybrid FTS/vector recall with a dependency-free local baseline and replaceable embedder.
- Retrieval-time quarantine for untrusted instruction-like content.
- Crash-recoverable Dream workers with leases, heartbeat renewal, retries, and replay-safe resume.
- An OpenAI-compatible structured-output provider that can only propose candidates.
- A five-tool STDIO MCP server: `recall`, `record`, `explain`, `correct`, and `forget`.
- User-approved Codex JSONL ingestion and a project-scoped Codex installer.
- A deterministic Obsidian-compatible Markdown review projection.
- First-class procedural episodes for bounded reuse of successful and failed coding attempts.
- Reproducible no-memory, Markdown, BM25, flat-context, and external/Graphify benchmark adapters.
- Cross-platform CI, package verification, and actionable `doctor` diagnostics.

The real engine currently passes all 12 public MemoryRotBench queries and all seven production
chaos contracts. The repository test suite has 196 passing tests at this checkpoint. In the first
fingerprinted public matrix, the strongest simple baselines pass 7/12 while MemoryGraph passes
12/12.

## Quick start

```bash
uv sync
uv run memorygraph init --database /tmp/memorygraph.db
uv run memorygraph doctor --database /tmp/memorygraph.db
uv run memorygraph bank create personal:founder --database /tmp/memorygraph.db
uv run memorygraph dogfood bootstrap --database /tmp/memorygraph.db
uv run memorygraph predicate define works_at \
  --bank personal:founder --cardinality one --volatility volatile \
  --database /tmp/memorygraph.db
```

Record evidence, then turn it into a claim:

```bash
OBSERVATION_ID=$(uv run memorygraph observe "Abrar works at Acme." \
  --bank personal:founder --source-key event:acme \
  --database /tmp/memorygraph.db)

CLAIM_ID=$(uv run memorygraph claim assert Abrar works_at Acme \
  --bank personal:founder --observation "$OBSERVATION_ID" \
  --database /tmp/memorygraph.db)
```

The Python API also exposes `confirm_claim`, `contradict_claim`, `supersede_claim`,
`recall`, `history`, and `explain` for embedded applications.

Record a coding attempt so future agents can reuse a successful strategy—or avoid a known
failure—without pretending it is universally applicable:

```bash
memorygraph record-attempt "Run migrations before starting the worker" \
  --bank project:my-app --source-key attempt:migrate-worker \
  --task "start durable worker" --outcome success \
  --applicability-json '{"database":"sqlite"}'
```

## Run the dream cycle

The embedded provider reads typed candidates from `metadata.memorygraph`, proposes graph
changes, validates evidence and temporal preconditions, then commits eligible proposals in
one transaction. Model providers implement the same candidate-only protocol and never get a
direct database write path.

```bash
PYTHONPATH=src:. uv run python examples/run_dream_cycle.py \
  --database /tmp/memorygraph-dream.db

uv run memorygraph dream status RUN_ID \
  --bank personal:founder --database /tmp/memorygraph-dream.db

uv run memorygraph dream reviews \
  --bank personal:founder --database /tmp/memorygraph-dream.db

uv run memorygraph dream rollback RUN_ID \
  --bank personal:founder --database /tmp/memorygraph-dream.db
```

For CLI ingestion, pass the candidate envelope with `observe --metadata-file FILE.json`, then
run `memorygraph dream run --bank BANK`. `--mode dry_run` validates and persists proposals but
does not consume the observation or change claims.

For durable execution, queue work and run a worker separately:

```bash
uv run memorygraph dream queue \
  --bank personal:founder --database /tmp/memorygraph-dream.db

uv run memorygraph dream worker \
  --bank personal:founder --database /tmp/memorygraph-dream.db
```

To use an OpenAI-compatible Responses endpoint, set the configured key variable and pass a
model to both the queue and worker. Provider output is parsed as strict structured data and still
passes through the same deterministic evidence and commit gates.

```bash
export OPENAI_API_KEY=...
uv run memorygraph dream queue --bank personal:founder \
  --provider-model YOUR_MODEL --database /tmp/memorygraph-dream.db
uv run memorygraph dream worker --bank personal:founder \
  --provider-model YOUR_MODEL --database /tmp/memorygraph-dream.db
```

## Connect Codex

Install the package, initialize a project bank, and add project-scoped MCP configuration:

```bash
uv run memorygraph init
uv run memorygraph bank create project:my-app
uv run memorygraph install-codex --project .
uv run memorygraph probe-codex --project .
```

The installer creates or repairs a `[mcp_servers.memorygraph]` block in `.codex/config.toml`,
points it at the current Python environment's `memorygraph.mcp` STDIO module, and configures Codex
to prompt for writes. It does not modify global Codex configuration. The five MCP operations
require explicit bank scope.

`probe-codex` validates the project config and exercises a real MCP subprocess lifecycle. Use
`--project-database` if you want the probe to hit the configured project database instead of
temporary disposable probe DBs.

Importing session content is opt-in. Each JSONL record must carry `bank`, `session_id`, `turn_id`,
`role`, `content`, and `approved`; unapproved records are skipped by default:

```bash
memorygraph ingest-codex approved-session.jsonl
```

## Dogfood Alpha

The official offline six-arm fixture matrix is:

```bash
PYTHONPATH=src:. uv run python examples/run_dogfood_fixture_matrix.py
```

For a real project, bootstrap the operating contract first:

```bash
uv run memorygraph dogfood bootstrap \
  --database .memorygraph/memory.db \
  --bank project:my-app \
  --workspace my-app
```

The fixture matrix runs these arms:

- `no_memory`
- `markdown`
- `memorygraph_graph_only`
- `memorygraph_gated_dream`
- `memorygraph_always_dream`
- `graphify_compatible`

It measures task pass/fail, useful recall precision, forbidden or stale recall leakage, repeated
mistakes, latency, token estimates, tool calls, retries, estimated cost fields, and Dream review
load. Results are written to `benchmarks/reports/dogfood-offline-mvp.json` and the append-only
ledger `benchmarks/reports/dogfood-offline-mvp.jsonl`.
Task pass/fail follows query expectations; Dream review backlog remains a separate, visible cost.

Current offline fixture result on `2026-08-22`:

- `memorygraph_always_dream`: `3/3`
- `memorygraph_graph_only`: `1/3`
- `memorygraph_gated_dream`: `1/3`
- `graphify_compatible`: `1/3`
- `markdown`: `1/3`, with forbidden-fragment leakage

`graphify_compatible` is a protocol adapter that lets an external retriever compete against the
same manifest, time bounds, and grading contract. It is not a claim that this repository has
already completed a live Graphify comparison.

## Human review in Obsidian

Generate a Markdown vault containing current claims, exact provenance, relations, and the Dream
review queue:

```bash
memorygraph project-obsidian --bank project:my-app \
  --output .memorygraph/obsidian
```

The Markdown is disposable and manifest-managed. SQLite observations and append-only events stay
authoritative; edits to generated notes never silently mutate memory.

## Why a graph?

The graph gives agents composable structure: entities are nodes and claims such as
`Abrar --works_at--> Stripe` are typed edges. MemoryGraph does not treat an edge as timeless
truth. Each claim version carries valid time, system time, lifecycle, provenance, and exact
source evidence. That is the difference between a useful memory graph and a stale fact store.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
PYTHONPATH=src:. python examples/run_memoryrotbench_memorygraph.py
PYTHONPATH=src:. python examples/run_memoryrotbench_chaos_memorygraph.py
python examples/run_memoryrotbench_baseline_matrix.py
PYTHONPATH=src:. uv run python examples/run_dogfood_fixture_matrix.py
```

Expected results: `12/12` public retrieval cases and `7/7` production chaos cases.
The matrix appends immutable, corpus- and evaluator-fingerprinted records to
`benchmarks/reports/public-baseline-matrix.jsonl`. Supply `--graphify-command` to run an external
Graphify adapter against exactly the same visible corpus and grading contract.

## Next product layer

The next gate is still real-world proof: live model-backed dogfood sessions, real Graphify
head-to-head comparisons, and design-partner usage over actual coding work. Full-pipeline deletion
residue is audited and reported; any identity residue that cannot be safely erased without
rewriting history is surfaced instead of hidden. The dream validator remains the safety waist
every provider and worker must pass through.

Licensed under Apache-2.0.
