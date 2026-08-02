from pathlib import Path

from supervisor.models import NextStep, Status, Task, TaskRun, WorkerResult
from supervisor.storage import RunStore


def test_store_persists_a_structured_run(tmp_path: Path):
    store = RunStore(tmp_path / "run.sqlite3")
    try:
        run = TaskRun(
            task=Task(task_id="T01", title="Test"),
            run_id="run-1",
            status=Status.NEEDS_USER_REVIEW,
            route="needs_user_review",
            worker_results=[WorkerResult(status=Status.NEEDS_USER_REVIEW, summary="Review", recommended_next_step=NextStep.ASK_USER)],
        )
        store.save(run)
        count = store._connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_checkpoint_survives_before_a_run_is_finalized(tmp_path: Path):
    store = RunStore(tmp_path / "run.sqlite3")
    try:
        previous = store.claim_task("D006", "run-in-progress", 999999)
        assert previous is None
        store.checkpoint(
            "run-in-progress",
            "D006",
            "qwen",
            "Qwen3 Coder",
            "heartbeat",
            {
                "session_id": "qwen-session-1",
                "next_action": "continue_from_last_agent_tool",
                "summary": "Agent last requested: run_shell_command.",
                "changed_files": ["lib/time.dart"],
                "diff_fingerprint": "abc123",
            },
        )
        state = store.state_for("D006")
        assert state is not None
        assert state["agent_session_id"] == "qwen-session-1"
        assert state["next_action"] == "continue_from_last_agent_tool"
        assert state["status"] == "implementing"
        assert store._connection.execute("SELECT COUNT(*) FROM run_checkpoints").fetchone()[0] == 1
    finally:
        store.close()


def test_non_passing_run_keeps_its_next_pipeline_stage(tmp_path: Path):
    store = RunStore(tmp_path / "run.sqlite3")
    try:
        store.claim_task("D006", "run-1", 999999)
        store.checkpoint("run-1", "D006", "qwen", "Qwen3 Coder", "stage_complete", {"next_action": "test"})
        run = TaskRun(
            task=Task(task_id="D006", title="Time"), run_id="run-1", status=Status.NEEDS_USER_REVIEW,
            route="needs_user_review", worker_results=[],
        )
        store.finish_task(run)
        assert store.state_for("D006")["status"] == "needs_user_review"
        assert store.state_for("D006")["next_action"] == "test"
    finally:
        store.close()
