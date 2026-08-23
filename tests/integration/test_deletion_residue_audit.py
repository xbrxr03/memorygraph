from __future__ import annotations

from hashlib import sha256

from memorygraph import MemoryGraph


def test_deletion_scrubs_indexes_embeddings_artifacts_and_projection(tmp_path) -> None:
    vault = tmp_path / "vault"
    secret = "The private launch code is ORBIT-9284 and must be rotated."
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        observation = memory.observe(
            secret,
            bank=bank.id,
            source_key="session:secret",
        )
        claim = memory.assert_claim(
            bank=bank.id,
            subject="Acme",
            predicate="launch_code",
            object="ORBIT-9284",
            object_kind="string",
            observation_id=observation.id,
        )
        artifact = memory.artifacts.create(
            id="artifact-secret",
            bank_id=bank.id,
            kind="runbook",
            artifact_key="launch",
            content=secret,
            source_claim_ids=[claim.id],
            source_watermark=memory.events.current_watermark(bank.id),
            generator_name="test",
            generator_version="1",
            created_at=claim.created_at,
        )
        memory.search.upsert(
            bank_id=bank.id,
            resource_type="artifact",
            resource_id=artifact.id,
            title="launch",
            body=secret,
            content_sha256=sha256(secret.encode()).hexdigest(),
            created_at=claim.created_at,
        )
        assert memory.embedder is not None
        memory.embeddings.replace(
            bank_id=bank.id,
            resource_type="artifact",
            resource_id=artifact.id,
            model=memory.embedder.name,
            content_sha256=sha256(secret.encode()).hexdigest(),
            vector=memory.embedder.embed((secret,))[0],
            created_at=claim.created_at,
        )
        memory.project_obsidian(bank=bank.id, output_directory=vault)
        assert secret in "\n".join(path.read_text(encoding="utf-8") for path in vault.rglob("*.md"))

        result = memory.delete_observation(observation.id, bank=bank.id)
        memory.project_obsidian(bank=bank.id, output_directory=vault)

        assert result.residue_issues == ()
        assert memory.search.get(bank.id, "claim", claim.id) is None
        assert memory.search.get(bank.id, "artifact", artifact.id) is None
        assert (
            memory.embeddings.list_for_bank(
                bank_id=bank.id,
                model=memory.embedder.name,
            )
            == ()
        )
        redacted_artifact = memory.artifacts.get(bank.id, artifact.id)
        assert redacted_artifact is not None
        assert redacted_artifact.content.startswith("[redacted derived artifact:")
        assert secret not in "\n".join(
            path.read_text(encoding="utf-8") for path in vault.rglob("*.md")
        )


def test_exact_residue_is_reported_in_entity_identity_instead_of_hidden(tmp_path) -> None:
    secret = "UNIQUE PRIVATE ENTITY 9f854"
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        observation = memory.observe(secret, bank=bank.id, source_key="session:entity")
        memory.assert_claim(
            bank=bank.id,
            subject=secret,
            predicate="exists",
            object=True,
            object_kind="boolean",
            observation_id=observation.id,
        )

        result = memory.delete_observation(observation.id, bank=bank.id)

        assert any(issue.startswith("entities:") for issue in result.residue_issues)
