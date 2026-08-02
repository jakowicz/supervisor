import pytest

from pathlib import Path

from supervisor import cli
from supervisor.cli import _can_recover_qwen, _collection_runbooks, _completion_banner, _expand_task_range, _initial_document, _qwen_session_to_resume, _recovered_qwen_event, _resume_stage, _run_registered_collections, _run_summary, _should_skip_accepted_task
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


def test_collection_runbooks_uses_declared_sequence_and_skips_supporting_markdown(tmp_path: Path):
    template = """---
task_id: {task_id}
sequence: {sequence}
title: Task
browser_impact: not_applicable
playwright_spec:
---

## Objective

Task

## Acceptance criteria

- It works.
"""
    (tmp_path / "F002.md").write_text(template.format(task_id="F002", sequence=2), encoding="utf-8")
    (tmp_path / "F001.md").write_text(template.format(task_id="F001", sequence=1), encoding="utf-8")
    (tmp_path / "README.md").write_text("supporting text", encoding="utf-8")
    (tmp_path / "PRODUCT_BRIEF.template.md").write_text("supporting text", encoding="utf-8")

    assert [path.stem for path in _collection_runbooks(tmp_path)] == ["F001", "F002"]


def test_initial_document_requires_and_loads_collection_context(tmp_path: Path):
    with pytest.raises(ValueError, match="INITIAL.md"):
        _initial_document(tmp_path)

    (tmp_path / "INITIAL.md").write_text("# Initial project brief\n\nBuild a task app.\n", encoding="utf-8")

    assert "Build a task app." in _initial_document(tmp_path)


def test_initial_document_uses_a_generated_project_brief_when_no_local_initial_exists(tmp_path: Path):
    generated_collection = tmp_path / "authoring-runbooks"
    generated_collection.mkdir()
    (tmp_path / "PROJECT_BRIEF.md").write_text("# Project brief\n\nBuild a task app.\n", encoding="utf-8")

    assert "Build a task app." in _initial_document(generated_collection)


def test_registered_collections_follow_explicit_children_recursively(tmp_path: Path, monkeypatch):
    parent = tmp_path / "source-runbooks"
    authoring = tmp_path / "project" / "authoring-runbooks"
    implementation = tmp_path / "project" / "runbooks"
    for directory in (parent / ".supervisor-children", authoring / ".supervisor-children", implementation):
        directory.mkdir(parents=True, exist_ok=True)
    (parent / ".supervisor-children" / "authoring.json").write_text(
        '{"runbooks_dir": "../project/authoring-runbooks"}', encoding="utf-8"
    )
    (authoring / ".supervisor-children" / "implementation.json").write_text(
        '{"runbooks_dir": "../runbooks"}', encoding="utf-8"
    )
    (authoring / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    (implementation / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    calls = []

    def run_collection(directory, dry_run, continue_on_nonpass, database_path, initial_context):
        calls.append((directory, database_path, initial_context))
        return True

    monkeypatch.setattr(cli, "_run_collection_until_complete", run_collection)

    _run_registered_collections(parent, dry_run=False, continue_on_nonpass=False)

    assert [call[0] for call in calls] == [authoring, implementation]
    assert all(call[1] == call[0].parent / ".supervisor" / "supervisor.sqlite3" for call in calls)
