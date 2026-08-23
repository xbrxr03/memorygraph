"""Import user-approved Codex session records into raw observations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memorygraph import MemoryGraph, ValidationError


@dataclass(frozen=True, slots=True)
class CodexSessionRecord:
    """One approved session record supplied by the host or user export."""

    bank: str
    session_id: str
    turn_id: str
    role: str
    content: str
    approved: bool
    created_at: str | None = None
    workspace: str | None = None
    actor_id: str | None = None
    kind: str = "import"
    trust_class: str = "owner_explicit"
    sensitivity: str = "normal"
    source_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> CodexSessionRecord:
        bank = _required_text(payload, "bank")
        session_id = _first_text(payload, "session_id", "thread_id", "task_id")
        turn_id = _first_text(payload, "turn_id", "message_id", "record_id")
        role = _required_text(payload, "role")
        content = _required_text(payload, "content")
        created_at = _optional_text(payload, "created_at", "observed_at")
        workspace = _optional_text(payload, "workspace")
        actor_id = _optional_text(payload, "actor_id", "author_id")
        source_key = _optional_text(payload, "source_key")
        kind = _optional_text(payload, "kind") or "import"
        trust_class = _optional_text(payload, "trust_class") or "owner_explicit"
        sensitivity = _optional_text(payload, "sensitivity") or "normal"
        approved = _approved_flag(payload)
        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValidationError("metadata must be an object")
        return cls(
            bank=bank,
            session_id=session_id,
            turn_id=turn_id,
            role=role,
            content=content,
            approved=approved,
            created_at=created_at,
            workspace=workspace,
            actor_id=actor_id,
            kind=kind,
            trust_class=trust_class,
            sensitivity=sensitivity,
            source_key=source_key,
            metadata=dict(metadata),
        )

    def resolved_source_key(self) -> str:
        if self.source_key is not None:
            return self.source_key
        return f"codex:{self.session_id}:{self.turn_id}:{self.role}"


@dataclass(frozen=True, slots=True)
class CodexSessionImportReport:
    """Outcome of one approved import pass."""

    imported_observation_ids: tuple[str, ...]
    skipped_records: tuple[str, ...]

    @property
    def imported_count(self) -> int:
        return len(self.imported_observation_ids)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_records)


class CodexSessionAdapter:
    """Ingest explicit host exports without scraping private Codex state."""

    def __init__(self, memory: MemoryGraph) -> None:
        self.memory = memory

    def ingest_jsonl(
        self,
        path: str | Path,
        *,
        require_approval: bool = True,
    ) -> CodexSessionImportReport:
        records: list[CodexSessionRecord] = []
        for line_number, raw_line in enumerate(
            Path(path).expanduser().resolve().read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise ValidationError(f"Invalid JSON on line {line_number}: {error}") from error
            if not isinstance(payload, dict):
                raise ValidationError(f"JSONL line {line_number} must be an object")
            records.append(CodexSessionRecord.from_mapping(payload))
        return self.ingest_records(records, require_approval=require_approval)

    def ingest_records(
        self,
        records: Iterable[CodexSessionRecord | Mapping[str, Any]],
        *,
        require_approval: bool = True,
    ) -> CodexSessionImportReport:
        imported: list[str] = []
        skipped: list[str] = []
        for raw_record in records:
            record = (
                raw_record
                if isinstance(raw_record, CodexSessionRecord)
                else CodexSessionRecord.from_mapping(raw_record)
            )
            record_key = f"{record.session_id}:{record.turn_id}"
            if require_approval and not record.approved:
                skipped.append(record_key)
                continue
            metadata = {
                **record.metadata,
                "workspace": record.workspace,
                "codex": {
                    "session_id": record.session_id,
                    "turn_id": record.turn_id,
                    "role": record.role,
                    "approved": record.approved,
                },
            }
            if metadata["workspace"] is None:
                metadata.pop("workspace")
            observation = self.memory.observe(
                content=record.content,
                bank=record.bank,
                source_key=record.resolved_source_key(),
                kind=record.kind,
                actor_type=record.role,
                actor_id=record.actor_id,
                observed_at=record.created_at,
                trust_class=record.trust_class,
                sensitivity=record.sensitivity,
                metadata=metadata,
            )
            imported.append(observation.id)
        return CodexSessionImportReport(
            imported_observation_ids=tuple(imported),
            skipped_records=tuple(skipped),
        )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValidationError(f"{key} must be a string")
        stripped = value.strip()
        return stripped or None
    return None


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _optional_text(payload, key)
        if value is not None:
            return value
    raise ValidationError(f"one of {', '.join(keys)} must be provided")


def _approved_flag(payload: Mapping[str, Any]) -> bool:
    if "approved" in payload:
        value = payload["approved"]
        if not isinstance(value, bool):
            raise ValidationError("approved must be a boolean")
        return value
    status = _optional_text(payload, "approval", "approval_status")
    if status is None:
        return False
    return status.lower() == "approved"
