# MemoryRotBench

MemoryRotBench is the deterministic acceptance and benchmark fixture set for MemoryGraph.

This initial package provides:

- A versioned scenario format: `memoryrotbench.scenario/v1`
- A compact public scenario corpus aligned to the dream-cycle acceptance contract
- A standard-library-only fixture loader and validator
- Deterministic retrieval baselines: no memory, Markdown runbook, latest-N, BM25, and flat context
- A strict JSON subprocess bridge for Graphify/external systems on the identical visible corpus
- An append-only experiment log with corpus and evaluator fingerprints
- A deterministic runner, retrieval graders, and report serializers
- A `MemoryGraphAdapter` protocol wrapper for future engine integration
- Dream-runtime protocol contracts for phase-2 acceptance tests
- A chaos corpus plus runner for DreamRuntime mutation-path acceptance
- Acceptance tests that execute today without requiring the product implementation

## Structure

```text
benchmarks/memoryrotbench/
├── adapters/
├── chaos_loader.py
├── chaos_runner.py
├── dream_contracts.py
├── graders/
├── reference_runtime.py
├── runner.py
├── results.py
├── schema/
├── scenarios/
│   ├── development/
│   ├── public/
│   └── heldout-manifest.json
└── scenario_loader.py
```

## Usage

```bash
python3 examples/list_memoryrotbench_public_scenarios.py
python3 examples/run_memoryrotbench_public_flat_context.py
python3 examples/run_memoryrotbench_chaos_reference.py
python3 -m unittest discover -s tests/acceptance -v
```

## Notes

- The loader performs schema and referential checks without external dependencies.
- The baseline adapters are retrieval-only fixtures, not answer models.
- The phase-2 dream acceptance cases are expressed as protocol tests against a minimal runtime contract that the engine can implement later.

## Plugging In The Real Runtime

Root can wire the production runtime by implementing the `DreamRuntime` protocol from `dream_contracts.py`:

- `snapshot() -> RuntimeSnapshot`
- `process_proposal(proposal, fail_after_validation=False) -> CommitOutcome`
- `rollback(run_id) -> CommitOutcome`
- `delete_evidence(observation_id) -> None`
- `refresh_artifact(artifact_id, body, source_claim_ids, source_artifact_ids=()) -> ArtifactRecord`

Then run:

```python
from benchmarks.memoryrotbench.chaos_loader import load_chaos_cases
from benchmarks.memoryrotbench.chaos_runner import ChaosRunner

cases = load_chaos_cases("benchmarks/memoryrotbench/scenarios/development")
result = ChaosRunner(ProductionRuntime).run(cases)
```
