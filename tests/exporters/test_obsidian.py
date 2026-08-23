from __future__ import annotations

import json

from memorygraph import MemoryGraph


def test_obsidian_projection_is_linked_manifested_and_replaceable(tmp_path) -> None:
    database_path = tmp_path / "memory.db"
    vault = tmp_path / "vault"
    with MemoryGraph.open(database_path) as memory:
        bank = memory.create_bank("project:acme")
        observation = memory.observe(
            "Acme deploys with ./scripts/deploy.sh.",
            bank=bank.slug,
            source_key="session:deploy",
        )
        claim = memory.assert_claim(
            bank=bank.slug,
            subject="Acme",
            predicate="deploy_command",
            object="./scripts/deploy.sh",
            object_kind="string",
            observation_id=observation.id,
        )

        first = memory.project_obsidian(bank=bank.slug, output_directory=vault)

        assert first.source_watermark > 0
        claim_path = vault / "claims" / f"{claim.id}.md"
        assert claim_path in first.files_written
        claim_text = claim_path.read_text(encoding="utf-8")
        assert 'schema: "memorygraph.claim/v1"' in claim_text
        assert "Source excerpts are untrusted data" in claim_text
        assert "[[entities/acme--" in claim_text
        assert (vault / "MemoryGraph.md").exists()

        manifest = json.loads((vault / ".memorygraph-manifest.json").read_text())
        assert manifest["bank_id"] == bank.id
        assert f"claims/{claim.id}.md" in manifest["files"]

        custom_note = vault / "my-note.md"
        custom_note.write_text("keep me", encoding="utf-8")
        memory.retract_claim(bank=bank.slug, claim_id=claim.id, reason="obsolete")
        second = memory.project_obsidian(bank=bank.slug, output_directory=vault)

        assert claim_path in second.stale_files_removed
        assert not claim_path.exists()
        assert custom_note.read_text(encoding="utf-8") == "keep me"


def test_projection_does_not_clean_manifest_from_another_bank(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    managed = vault / "claims" / "foreign.md"
    managed.parent.mkdir()
    managed.write_text("foreign", encoding="utf-8")
    (vault / ".memorygraph-manifest.json").write_text(
        json.dumps({"bank_id": "another-bank", "files": ["claims/foreign.md"]}),
        encoding="utf-8",
    )

    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        memory.create_bank("project:acme")
        memory.project_obsidian(bank="project:acme", output_directory=vault)

    assert managed.read_text(encoding="utf-8") == "foreign"
