import pytest

from pathlib import Path

from supervisor.cli import _can_recover_qwen, _completion_banner, _expand_task_range, _qwen_session_to_resume, _recovered_qwen_event, _resume_stage, _run_summary, _should_skip_accepted_task
from supervisor.models import NextStep, RunEvent, Status, Task, TaskRun, WorkerResult


def test_resume_starts_at_the_saved_next_stage():
    assert _resume_stage({"status": "interrupted", "next_action": "test"}) == "test"
    assert _resume_stage({"status": "interrupted", "next_action": "codex_final"}) == "codex_final"
    assert _resume_stage({"status": "validating", "next_action": "browser"}) == "browser"
    assert _resume_stage({"status": "accepted", "next_action": "qwen"}) == "prepare"
    assert _resume_stage({"status": "interrupted", "next_action": "unexpected"}) == "prepare"


def test_resume_restores_a_qwen_session_for_an_unfinished_task():
    assert _qwen_session_to_resume({"status": "interrupted", "agent_session_id": "qwen-session"}) == "qwen-session"
    assert _qwen_session_to_resume({"status": "accepted", "agent_session_id": "qwen-session"}) is None
    assert _qwen_session_to_resume({"status": "interrupted"}) is None


def test_retry_reopens_an_accepted_task_for_verification(monkeypatch, tmp_path):
    state = {"status": "accepted", "accepted_commit": "abc123"}
    monkeypatch.setattr("supervisor.cli._commit_exists", lambda *_args: True)

    assert _should_skip_accepted_task(state, False, tmp_path) is True
    assert _should_skip_accepted_task(state, True, tmp_path) is False


def test_valid_qwen_evidence_can_be_recovered_from_any_unfinished_state():
    assert _can_recover_qwen({"status": "interrupted"}) is True
    assert _can_recover_qwen({"status": "implementing"}) is True
    assert _can_recover_qwen({"status": "validating"}) is True
    assert _can_recover_qwen({"status": "accepted"}) is False
    assert _can_recover_qwen(None) is False


def test_recovered_qwen_pass_routes_to_codex_final_review():
    event = _recovered_qwen_event(
        WorkerResult(
            status=Status.PASS,
            summary="Recovered implementation",
            recommended_next_step=NextStep.COMPLETE,
        )
    )

    assert event.stage == "qwen"
    assert event.route == "codex_final"


def test_compact_run_summary_excludes_raw_worker_evidence(tmp_path):
    result = WorkerResult(
        status=Status.PASS,
        summary="Completed",
        recommended_next_step=NextStep.COMPLETE,
    )
    result.evidence.agent_log = "very large raw transcript"
    run = TaskRun(
        run_id="run-42",
        task=Task(task_id="M001", title="Mock"),
        status=Status.PASS,
        route="accepted",
        worker_results=[result],
        events=[
            RunEvent(
                stage="qwen",
                agent="Qwen",
                model="local",
                attempt=1,
                status=Status.PASS,
                summary="Completed",
                route="test",
                result=result,
            )
        ],
    )

    summary = _run_summary(run, Path(tmp_path) / "supervisor.sqlite3")

    assert "very large raw transcript" not in summary
    assert "supervisor-reports show run-42" in summary
    assert summary.startswith("SUCCESS — TASK ACCEPTED")


def test_completion_banner_marks_non_pass_runs_as_not_accepted():
    run = TaskRun(
        run_id="run-43",
        task=Task(task_id="D010", title="Release journey"),
        status=Status.NEEDS_USER_REVIEW,
        route="needs_user_review",
        worker_results=[],
        events=[],
    )

    assert _completion_banner(run).startswith("NOT ACCEPTED — USER REVIEW REQUIRED")


def test_task_range_expands_inclusive_zero_padded_task_ids():
    assert _expand_task_range("D007-D010") == ["D007", "D008", "D009", "D010"]
    with pytest.raises(ValueError):
        _expand_task_range("D010-D007")
    with pytest.raises(ValueError):
        _expand_task_range("D007-T010")
