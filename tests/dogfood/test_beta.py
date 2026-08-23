from __future__ import annotations

from memorygraph.dogfood import LiveSessionEvent, LiveSessionLedger, evaluate_live_sessions


def _event(**overrides: object) -> LiveSessionEvent:
    values = {
        "session_id": "session-1",
        "task_key": "fix-probe",
        "event_type": "recall",
        "approved": True,
        "created_at": "2026-08-22T12:00:00Z",
    }
    values.update(overrides)
    return LiveSessionEvent(**values)  # type: ignore[arg-type]


def test_live_ledger_is_append_only_and_requires_approval(tmp_path) -> None:
    ledger = LiveSessionLedger(tmp_path / "live.jsonl")
    ledger.append(_event(recalled_ids=("a",), useful_ids=("a",)))

    assert ledger.read()[0].recalled_ids == ("a",)

    try:
        ledger.append(_event(approved=False))
    except ValueError as error:
        assert "approval" in str(error)
    else:  # pragma: no cover
        raise AssertionError("unapproved instrumentation must not be persisted")


def test_live_evaluator_scores_recall_tasks_and_repeated_mistakes() -> None:
    report = evaluate_live_sessions(
        (
            _event(recalled_ids=("good", "bad"), useful_ids=("good",), forbidden_ids=("bad",)),
            _event(event_type="attempt", outcome="failure", mistake_key="same"),
            _event(event_type="attempt", outcome="failure", mistake_key="same"),
            _event(event_type="task", outcome="success", task_key="fix-probe"),
        )
    )

    assert report["sessions"] == 1
    assert report["successful_tasks"] == 1
    assert report["useful_recall_precision"] == 0.5
    assert report["forbidden_recall_hits"] == 1
    assert report["repeated_mistakes"] == 1
