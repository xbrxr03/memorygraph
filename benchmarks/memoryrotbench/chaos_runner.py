"""Deterministic runner for DreamRuntime chaos fixtures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from .chaos_loader import (
    ChaosCase,
    ChaosSnapshotExpectation,
    build_proposal,
)
from .dream_contracts import DreamRuntime, RuntimeSnapshot
from .results import make_run_id, write_json_report


@dataclass(frozen=True)
class ChaosStepResult:
    step_id: str
    step_type: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ChaosCaseResult:
    case_id: str
    acceptance_case: int
    passed: bool
    step_results: tuple[ChaosStepResult, ...]
    final_snapshot: RuntimeSnapshot


@dataclass(frozen=True)
class ChaosRunResult:
    run_id: str
    case_results: tuple[ChaosCaseResult, ...]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "summary": {
                "case_count": len(self.case_results),
                "passed_cases": sum(1 for case in self.case_results if case.passed),
                "failed_cases": sum(1 for case in self.case_results if not case.passed),
            },
            "cases": [
                {
                    "case_id": case.case_id,
                    "acceptance_case": case.acceptance_case,
                    "passed": case.passed,
                    "step_results": [asdict(step) for step in case.step_results],
                    "final_snapshot": {
                        "version": case.final_snapshot.version,
                        "active_claim_ids": list(case.final_snapshot.active_claim_ids),
                        "evidence_ids": list(case.final_snapshot.evidence_ids),
                        "current_view": [list(item) for item in case.final_snapshot.current_view],
                        "artifact_ids": list(case.final_snapshot.artifact_ids),
                        "committed_run_ids": list(case.final_snapshot.committed_run_ids),
                        "history_claim_ids": list(case.final_snapshot.history_claim_ids),
                    },
                }
                for case in self.case_results
            ],
        }

    def to_text(self) -> str:
        passed = sum(1 for case in self.case_results if case.passed)
        return "\n".join(
            [
                f"Run ID: {self.run_id}",
                f"Cases: {len(self.case_results)}",
                f"Passed: {passed}",
                f"Failed: {len(self.case_results) - passed}",
            ]
        )

    def write_json_report(self, path: str) -> None:
        write_json_report(path, self.to_dict())


class ChaosRunner:
    def __init__(self, runtime_factory: Callable[[dict[str, str]], DreamRuntime]) -> None:
        self.runtime_factory = runtime_factory

    def run(self, cases: list[ChaosCase], *, run_id: str | None = None) -> ChaosRunResult:
        active_run_id = run_id or make_run_id("memoryrotbench-chaos")
        case_results = tuple(self._run_case(case) for case in cases)
        return ChaosRunResult(run_id=active_run_id, case_results=case_results)

    def _run_case(self, case: ChaosCase) -> ChaosCaseResult:
        runtime = self.runtime_factory(
            {observation.observation_id: observation.content for observation in case.observations}
        )
        step_results: list[ChaosStepResult] = []
        run_refs: dict[str, str] = {}
        artifact_refs: dict[str, str] = {}
        snapshot_refs: dict[str, RuntimeSnapshot] = {}
        passed = True

        for step in case.steps:
            try:
                message = self._execute_step(runtime, step, run_refs, artifact_refs, snapshot_refs)
                step_results.append(
                    ChaosStepResult(
                        step_id=step.step_id,
                        step_type=step.step_type,
                        passed=True,
                        message=message,
                    )
                )
            except AssertionError as exc:
                passed = False
                step_results.append(
                    ChaosStepResult(
                        step_id=step.step_id,
                        step_type=step.step_type,
                        passed=False,
                        message=str(exc),
                    )
                )
                break

        final_snapshot = runtime.snapshot()
        if passed and case.final_assertion is not None:
            try:
                _assert_snapshot(final_snapshot, case.final_assertion, run_refs, artifact_refs)
                step_results.append(
                    ChaosStepResult(
                        step_id="final_assertion",
                        step_type="final_assertion",
                        passed=True,
                        message="final snapshot matched expectation",
                    )
                )
            except AssertionError as exc:
                passed = False
                step_results.append(
                    ChaosStepResult(
                        step_id="final_assertion",
                        step_type="final_assertion",
                        passed=False,
                        message=str(exc),
                    )
                )

        return ChaosCaseResult(
            case_id=case.case_id,
            acceptance_case=case.acceptance_case,
            passed=passed,
            step_results=tuple(step_results),
            final_snapshot=final_snapshot,
        )

    def _execute_step(
        self,
        runtime: DreamRuntime,
        step,
        run_refs: dict[str, str],
        artifact_refs: dict[str, str],
        snapshot_refs: dict[str, RuntimeSnapshot],
    ) -> str:
        raw = step.raw
        if step.step_type == "capture_snapshot":
            if step.label is None:
                raise AssertionError("capture_snapshot requires a label")
            snapshot_refs[step.label] = runtime.snapshot()
            return f"captured snapshot {step.label}"

        if step.step_type == "process_proposal":
            expected = raw["expect"]
            try:
                outcome = runtime.process_proposal(
                    build_proposal(step),
                    fail_after_validation=bool(raw.get("fail_after_validation", False)),
                )
            except Exception as exc:  # noqa: BLE001
                expected_exception = expected.get("exception")
                assert expected_exception == exc.__class__.__name__, (
                    f"expected exception {expected_exception}, got {exc.__class__.__name__}"
                )
                return f"raised {exc.__class__.__name__}"
            expected_status = expected.get("status")
            assert outcome.status == expected_status, (
                f"expected status {expected_status}, got {outcome.status}"
            )
            if step.label and outcome.run_id is not None:
                run_refs[step.label] = outcome.run_id
            return f"process_proposal -> {outcome.status}"

        if step.step_type == "rollback":
            expected = raw["expect"]
            run_id = raw.get("run_id")
            if run_id is None:
                run_ref = raw["run_ref"]
                run_id = run_refs[run_ref]
            outcome = runtime.rollback(run_id)
            expected_status = expected.get("status")
            assert outcome.status == expected_status, (
                f"expected status {expected_status}, got {outcome.status}"
            )
            if step.label and outcome.run_id is not None:
                run_refs[step.label] = outcome.run_id
            return f"rollback -> {outcome.status}"

        if step.step_type == "delete_evidence":
            runtime.delete_evidence(raw["observation_id"])
            return f"deleted evidence {raw['observation_id']}"

        if step.step_type == "refresh_artifact":
            expected = raw["expect"]
            artifact_refs_raw = tuple(
                artifact_refs[label] for label in raw.get("source_artifact_refs", [])
            )
            try:
                artifact = runtime.refresh_artifact(
                    raw["artifact_id"],
                    body=raw["body"],
                    source_claim_ids=tuple(raw.get("source_claim_ids", [])),
                    source_artifact_ids=artifact_refs_raw,
                )
            except Exception as exc:  # noqa: BLE001
                expected_exception = expected.get("exception")
                assert expected_exception == exc.__class__.__name__, (
                    f"expected exception {expected_exception}, got {exc.__class__.__name__}"
                )
                return f"raised {exc.__class__.__name__}"
            expected_status = expected.get("status")
            assert expected_status == "ok", f"expected status ok, got {expected_status}"
            if step.label:
                artifact_refs[step.label] = artifact.artifact_id
            return f"refresh_artifact -> {artifact.artifact_id}"

        if step.step_type == "assert_snapshot":
            _assert_snapshot(
                runtime.snapshot(),
                _expectation_from_raw(raw["expect"]),
                run_refs,
                artifact_refs,
            )
            return "snapshot matched expectation"

        raise AssertionError(f"unsupported step type: {step.step_type}")


def _expectation_from_raw(raw: dict) -> ChaosSnapshotExpectation:
    return ChaosSnapshotExpectation(
        version=raw.get("version"),
        active_claim_ids=tuple(raw["active_claim_ids"]) if "active_claim_ids" in raw else None,
        evidence_ids=tuple(raw["evidence_ids"]) if "evidence_ids" in raw else None,
        current_view=(
            tuple(tuple(item) for item in raw["current_view"]) if "current_view" in raw else None
        ),
        artifact_ids=tuple(raw["artifact_ids"]) if "artifact_ids" in raw else None,
        committed_run_ids=(tuple(raw["committed_run_ids"]) if "committed_run_ids" in raw else None),
        history_claim_ids=tuple(raw["history_claim_ids"]) if "history_claim_ids" in raw else None,
    )


def _assert_snapshot(
    snapshot: RuntimeSnapshot,
    expected: ChaosSnapshotExpectation,
    run_refs: dict[str, str],
    artifact_refs: dict[str, str],
) -> None:
    if expected.version is not None:
        assert snapshot.version == expected.version, (
            f"expected version {expected.version}, got {snapshot.version}"
        )
    if expected.active_claim_ids is not None:
        assert snapshot.active_claim_ids == expected.active_claim_ids, (
            "expected active_claim_ids "
            f"{expected.active_claim_ids}, got {snapshot.active_claim_ids}"
        )
    if expected.evidence_ids is not None:
        assert snapshot.evidence_ids == expected.evidence_ids, (
            f"expected evidence_ids {expected.evidence_ids}, got {snapshot.evidence_ids}"
        )
    if expected.current_view is not None:
        assert snapshot.current_view == expected.current_view, (
            f"expected current_view {expected.current_view}, got {snapshot.current_view}"
        )
    if expected.artifact_ids is not None:
        resolved = tuple(artifact_refs.get(item, item) for item in expected.artifact_ids)
        assert snapshot.artifact_ids == resolved, (
            f"expected artifact_ids {resolved}, got {snapshot.artifact_ids}"
        )
    if expected.committed_run_ids is not None:
        resolved = tuple(run_refs.get(item, item) for item in expected.committed_run_ids)
        assert snapshot.committed_run_ids == resolved, (
            f"expected committed_run_ids {resolved}, got {snapshot.committed_run_ids}"
        )
    if expected.history_claim_ids is not None:
        assert snapshot.history_claim_ids == expected.history_claim_ids, (
            "expected history_claim_ids "
            f"{expected.history_claim_ids}, got {snapshot.history_claim_ids}"
        )
