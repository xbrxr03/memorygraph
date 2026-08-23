from memorygraph import MemoryGraph
from memorygraph.application import DurableWorkerService


def test_queue_dream_and_worker_complete_the_same_validated_pipeline(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        memory.define_predicate(
            "runtime",
            bank=bank.id,
            cardinality="one",
            volatility="durable",
        )
        observation = memory.observe(
            "Acme uses Python 3.13.",
            bank=bank.id,
            source_key="session:runtime",
            metadata={
                "memorygraph": {
                    "entities": [{"local_id": "project", "name": "Acme", "type": "project"}],
                    "claims": [
                        {
                            "local_id": "runtime",
                            "subject": "project",
                            "predicate": "runtime",
                            "object": {"kind": "string", "value": "Python 3.13"},
                            "confidence": 1.0,
                        }
                    ],
                }
            },
        )

        run, task = memory.queue_dream(
            bank=bank.id,
            observation_ids=(observation.id,),
        )

        assert run.state == "queued"
        assert task.state == "queued"
        assert memory.observations.get(bank.id, observation.id).ingestion_state == "pending"

        result = DurableWorkerService(memory, worker_id="worker:test").process_next(bank=bank.id)

        assert result is not None
        assert result.run_id == run.id
        assert result.state == "completed"
        current = memory.history(
            bank=bank.id,
            subject="Acme",
            predicate="runtime",
            current_versions_only=True,
        )
        assert len(current) == 1
        assert current[0].object == "Python 3.13"
