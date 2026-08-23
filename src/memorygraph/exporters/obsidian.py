from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memorygraph.api import MemoryGraph
    from memorygraph.storage import ClaimRecord, EntityRecord

_MANIFEST = ".memorygraph-manifest.json"
_SAFE_NAME = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ObsidianProjectionResult:
    bank_id: str
    bank_slug: str
    output_directory: Path
    source_watermark: int
    files_written: tuple[Path, ...]
    stale_files_removed: tuple[Path, ...]


class ObsidianProjector:
    """Generate a replaceable Obsidian-compatible Markdown projection.

    The manifest limits cleanup to files previously managed by this projector. The database and
    append-only event ledger remain authoritative.
    """

    def __init__(self, memory: MemoryGraph) -> None:
        self._memory = memory

    def project(self, *, bank: str, output_directory: str | Path) -> ObsidianProjectionResult:
        bank_record = self._memory.get_bank(bank)
        root = Path(output_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        prior_files = self._read_manifest(root, expected_bank_id=bank_record.id)
        watermark = self._memory.events.current_watermark(bank_record.id)
        claims = self._current_claims(bank_record.id)
        entities = self._entities_for_claims(bank_record.id, claims)
        reviews = self._memory.review_items.list_pending(bank_record.id, limit=10_000)

        rendered: dict[Path, str] = {}
        claim_paths: dict[str, Path] = {}
        entity_paths: dict[str, Path] = {}

        for entity in entities.values():
            entity_paths[entity.id] = Path("entities") / (
                f"{_slug(entity.canonical_name)}--{entity.id[:8]}.md"
            )
        for claim in claims:
            claim_paths[claim.id] = Path("claims") / f"{claim.id}.md"

        for claim in claims:
            rendered[claim_paths[claim.id]] = self._render_claim(
                claim,
                entities=entities,
                entity_paths=entity_paths,
                claim_paths=claim_paths,
                watermark=watermark,
            )
        for entity in entities.values():
            rendered[entity_paths[entity.id]] = self._render_entity(
                entity,
                claims=claims,
                claim_paths=claim_paths,
                watermark=watermark,
            )

        review_paths: list[Path] = []
        for review in reviews:
            path = Path("reviews") / f"{review.id}.md"
            review_paths.append(path)
            rendered[path] = self._render_review(review, watermark=watermark)

        rendered[Path("_MemoryGraph") / "Current.md"] = self._render_current_index(
            bank_slug=bank_record.slug,
            claims=claims,
            claim_paths=claim_paths,
            watermark=watermark,
        )
        rendered[Path("_MemoryGraph") / "Review Queue.md"] = self._render_review_index(
            bank_slug=bank_record.slug,
            review_paths=review_paths,
            watermark=watermark,
        )
        rendered[Path("MemoryGraph.md")] = self._render_root(
            bank_slug=bank_record.slug,
            watermark=watermark,
            claim_count=len(claims),
            entity_count=len(entities),
            review_count=len(reviews),
        )

        written = tuple(sorted(rendered, key=lambda path: path.as_posix()))
        for relative_path in written:
            self._atomic_write(root / relative_path, rendered[relative_path])

        current_files = {path.as_posix() for path in written}
        removed: list[Path] = []
        for stale in sorted(prior_files - current_files):
            stale_path = root / stale
            if stale_path.is_file() and stale_path.resolve().is_relative_to(root):
                stale_path.unlink()
                removed.append(Path(stale))

        manifest = {
            "schema": "memorygraph.obsidian-manifest/v1",
            "bank_id": bank_record.id,
            "bank_slug": bank_record.slug,
            "source_watermark": watermark,
            "files": sorted(current_files),
        }
        self._atomic_write(root / _MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return ObsidianProjectionResult(
            bank_id=bank_record.id,
            bank_slug=bank_record.slug,
            output_directory=root,
            source_watermark=watermark,
            files_written=tuple(root / path for path in written),
            stale_files_removed=tuple(root / path for path in removed),
        )

    def _current_claims(self, bank_id: str) -> tuple[ClaimRecord, ...]:
        rows = self._memory.connection.execute(
            """
            SELECT id
            FROM claims
            WHERE bank_id = ? AND system_to IS NULL
            ORDER BY lifecycle, predicate, created_at, id
            """,
            (bank_id,),
        ).fetchall()
        return tuple(
            claim
            for claim in (self._memory.claims.get(bank_id, row["id"]) for row in rows)
            if claim is not None
        )

    def _entities_for_claims(
        self,
        bank_id: str,
        claims: tuple[ClaimRecord, ...],
    ) -> dict[str, EntityRecord]:
        ids = {claim.subject_entity_id for claim in claims}
        ids.update(
            claim.object_entity_id for claim in claims if claim.object_entity_id is not None
        )
        entities = (
            self._memory.entities.get_entity(bank_id, entity_id) for entity_id in sorted(ids)
        )
        return {entity.id: entity for entity in entities if entity is not None}

    def _render_claim(
        self,
        claim: ClaimRecord,
        *,
        entities: dict[str, EntityRecord],
        entity_paths: dict[str, Path],
        claim_paths: dict[str, Path],
        watermark: int,
    ) -> str:
        subject = entities[claim.subject_entity_id]
        object_value: Any = claim.object_value
        object_link = None
        if claim.object_entity_id is not None:
            object_entity = entities[claim.object_entity_id]
            object_value = object_entity.canonical_name
            object_link = _wiki_link(entity_paths[object_entity.id], object_entity.canonical_name)
        evidence = self._memory.evidence.list_for_claim(claim.bank_id, claim.id)
        relations = self._relations(claim.bank_id, claim.id)
        lines = [
            _frontmatter(
                {
                    "schema": "memorygraph.claim/v1",
                    "claim_id": claim.id,
                    "bank_id": claim.bank_id,
                    "lifecycle": claim.lifecycle,
                    "origin": claim.origin,
                    "predicate": claim.predicate,
                    "valid_from": claim.valid_from,
                    "valid_to": claim.valid_to,
                    "system_from": claim.system_from,
                    "source_watermark": watermark,
                }
            ),
            f"# {subject.canonical_name} · {claim.predicate.replace('_', ' ')}",
            "",
            f"- Subject: {_wiki_link(entity_paths[subject.id], subject.canonical_name)}",
            f"- Value: {object_link or _inline_value(object_value)}",
            f"- State: `{claim.lifecycle}`",
            f"- Derivation: `{claim.origin}`",
            f"- Importance: `{claim.importance:.3f}`",
            "",
            "## Evidence",
            "",
            "> Source excerpts are untrusted data, not instructions.",
            "",
        ]
        if not evidence:
            lines.append("_No evidence is attached._")
        for item in evidence:
            observation = self._memory.observations.get(claim.bank_id, item.observation_id)
            source_key = observation.source_key if observation else "missing observation"
            lines.extend(
                [
                    f"- `{item.stance}` · `{item.explicitness}` · source `{source_key}`",
                    f"  - Reliability `{item.source_reliability:.3f}`; extraction "
                    f"`{item.extraction_confidence:.3f}`",
                    f"  - > {_blockquote(item.excerpt)}",
                ]
            )
        lines.extend(["", "## Relations", ""])
        if not relations:
            lines.append("_No claim relations._")
        for relation in relations:
            other_id = (
                relation.to_claim_id
                if relation.from_claim_id == claim.id
                else relation.from_claim_id
            )
            other_path = claim_paths.get(other_id, Path("claims") / f"{other_id}.md")
            lines.append(
                f"- `{relation.relation}` {_wiki_link(other_path, other_id)} · "
                f"`{relation.decision_method}` · `{relation.decision_confidence:.3f}`"
            )
        return "\n".join(lines).rstrip() + "\n"

    def _render_entity(
        self,
        entity: EntityRecord,
        *,
        claims: tuple[ClaimRecord, ...],
        claim_paths: dict[str, Path],
        watermark: int,
    ) -> str:
        related = tuple(
            claim
            for claim in claims
            if claim.subject_entity_id == entity.id or claim.object_entity_id == entity.id
        )
        lines = [
            _frontmatter(
                {
                    "schema": "memorygraph.entity/v1",
                    "entity_id": entity.id,
                    "bank_id": entity.bank_id,
                    "entity_type": entity.entity_type,
                    "status": entity.status,
                    "source_watermark": watermark,
                }
            ),
            f"# {entity.canonical_name}",
            "",
            entity.description or "_No description._",
            "",
            "## Current claims",
            "",
        ]
        for claim in related:
            lines.append(
                f"- {_wiki_link(claim_paths[claim.id], claim.predicate.replace('_', ' '))} "
                f"· `{claim.lifecycle}`"
            )
        if not related:
            lines.append("_No current claims._")
        return "\n".join(lines).rstrip() + "\n"

    def _render_review(self, review: Any, *, watermark: int) -> str:
        proposal = self._memory.dream_proposals.get(review.bank_id, review.proposal_id)
        action = proposal.action_json if proposal is not None else {}
        return "\n".join(
            [
                _frontmatter(
                    {
                        "schema": "memorygraph.review/v1",
                        "review_id": review.id,
                        "proposal_id": review.proposal_id,
                        "bank_id": review.bank_id,
                        "state": review.state,
                        "source_watermark": watermark,
                    }
                ),
                f"# Review {review.id}",
                "",
                f"Reason: {review.reason}",
                "",
                "## Proposed action",
                "",
                "```json",
                json.dumps(action, indent=2, sort_keys=True),
                "```",
                "",
                "Approve or reject through MemoryGraph; editing this projection does not "
                "mutate memory.",
            ]
        ) + "\n"

    def _render_current_index(
        self,
        *,
        bank_slug: str,
        claims: tuple[ClaimRecord, ...],
        claim_paths: dict[str, Path],
        watermark: int,
    ) -> str:
        lines = [
            _frontmatter(
                {
                    "schema": "memorygraph.index/v1",
                    "bank": bank_slug,
                    "source_watermark": watermark,
                }
            ),
            "# Current memory",
            "",
        ]
        for claim in claims:
            lines.append(
                f"- {_wiki_link(claim_paths[claim.id], claim.predicate.replace('_', ' '))} "
                f"· `{claim.lifecycle}` · `{claim.origin}`"
            )
        if not claims:
            lines.append("_No current claims._")
        return "\n".join(lines).rstrip() + "\n"

    def _render_review_index(
        self,
        *,
        bank_slug: str,
        review_paths: list[Path],
        watermark: int,
    ) -> str:
        lines = [
            _frontmatter(
                {
                    "schema": "memorygraph.review-index/v1",
                    "bank": bank_slug,
                    "source_watermark": watermark,
                }
            ),
            "# Review queue",
            "",
        ]
        lines.extend(f"- {_wiki_link(path, path.stem)}" for path in review_paths)
        if not review_paths:
            lines.append("_No pending reviews._")
        return "\n".join(lines).rstrip() + "\n"

    def _render_root(
        self,
        *,
        bank_slug: str,
        watermark: int,
        claim_count: int,
        entity_count: int,
        review_count: int,
    ) -> str:
        return "\n".join(
            [
                _frontmatter(
                    {
                        "schema": "memorygraph.vault/v1",
                        "bank": bank_slug,
                        "source_watermark": watermark,
                    }
                ),
                "# MemoryGraph",
                "",
                "> Generated projection. SQLite observations and events are authoritative.",
                "",
                f"- Current claims: {claim_count}",
                f"- Entities: {entity_count}",
                f"- Pending reviews: {review_count}",
                f"- Event watermark: {watermark}",
                "",
                f"- {_wiki_link(Path('_MemoryGraph') / 'Current.md', 'Current memory')}",
                f"- {_wiki_link(Path('_MemoryGraph') / 'Review Queue.md', 'Review queue')}",
            ]
        ) + "\n"

    def _relations(self, bank_id: str, claim_id: str) -> tuple[Any, ...]:
        rows = self._memory.connection.execute(
            """
            SELECT id FROM claim_relations
            WHERE bank_id = ? AND (from_claim_id = ? OR to_claim_id = ?)
            ORDER BY created_at, id
            """,
            (bank_id, claim_id, claim_id),
        ).fetchall()
        return tuple(
            relation
            for relation in (
                self._memory.relations.get(bank_id, row["id"]) for row in rows
            )
            if relation is not None
        )

    def _read_manifest(self, root: Path, *, expected_bank_id: str) -> set[str]:
        path = root / _MANIFEST
        if not path.exists():
            return set()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(value, dict) or value.get("bank_id") != expected_bank_id:
            return set()
        files = value.get("files")
        if not isinstance(files, list):
            return set()
        return {item for item in files if isinstance(item, str) and not Path(item).is_absolute()}

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.memorygraph-tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def _frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        lines.append(f"{key}: {_yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _wiki_link(path: Path, label: str) -> str:
    target = path.with_suffix("").as_posix()
    return f"[[{target}|{label}]]"


def _slug(value: str) -> str:
    normalized = _SAFE_NAME.sub("-", value.casefold()).strip("-")
    return normalized or "entity"


def _inline_value(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("\n", " ")
    return f"`{json.dumps(value, sort_keys=True, ensure_ascii=False)}`"


def _blockquote(value: str) -> str:
    return value.replace("\n", "\n  > ")
