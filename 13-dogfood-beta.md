# MemoryGraph Dogfood Beta

**Status:** Started August 22, 2026  
**Bank:** `project:memorygraph`  
**Workspace:** `agent-memory-research`

## Acceptance contract

Dogfood Beta is the first live proof loop for this repository. It is complete only when:

1. Codex launches the repository-scoped MemoryGraph MCP against `.memorygraph/memory.db`.
2. The live MCP lifecycle verifies initialize, the five-tool surface, record, recall, and forget.
3. Real coding-session evidence is captured only through explicit approved events.
4. A reproducible report measures task success, recall precision, forbidden recall, repeated
   mistakes, latency, tokens, tool calls, and retries.
5. At least five multi-session coding projects provide sustained evidence before the Milestone 6
   exit claim is made.

The database, live ledgers, and reports stay under ignored `.memorygraph/`; no private Codex task
history is scraped or committed.

## Live event schema

`memorygraph dogfood capture` accepts one JSON object with these required fields:

- `session_id`, `task_key`, `event_type`, `approved`, and `created_at`.
- `event_type` is `recall`, `attempt`, or `task`.
- Recall events may include `query`, `recalled_ids`, `useful_ids`, and `forbidden_ids`.
- Attempt events may include `outcome` and `mistake_key`.
- Any event may include non-negative `latency_ms`, `token_estimate`, `tool_calls`, and `retries`.

Unapproved events are rejected. The ledger is append-only JSONL with schema
`memorygraph.dogfood.live-event/v1`.

## Operator workflow

```bash
uv run memorygraph dogfood bootstrap --database .memorygraph/memory.db \
  --bank project:memorygraph --workspace agent-memory-research
uv run memorygraph install-codex --project .
uv run memorygraph probe-codex --project . --project-database --configured-only

uv run memorygraph dogfood capture \
  '{"session_id":"codex-task-id","task_key":"task-name","event_type":"task",\
"approved":true,"created_at":"2026-08-22T12:00:00Z","outcome":"success"}'

make dogfood-live
```

The probe intentionally deletes its own temporary observation through the public MCP `forget`
tool. A passing probe is lifecycle evidence, not retained product evidence.

## Accelerated gate

`make dogfood-beta` is the fast, repeatable pre-adoption gate. It simulates three time-separated
sessions across MCP, retrieval, Dream, storage, and CLI workstreams; compares no-memory, Markdown,
and MemoryGraph arms; and incorporates the public isolation/poisoning corpus plus all seven
production chaos cases. The command fails if any required gate fails and writes a fingerprinted
JSON report and append-only experiment ledger under `benchmarks/reports/`.

Passing this gate supports the claim “accelerated deterministic multi-session evidence passed.”
It does not replace the later claim that real users or projects sustained weekly usage.
