import json
from pathlib import Path

from supervisor.models import NextStep, RunEvent, Status, Task, TaskRun, WorkerResult
from supervisor.reports_cli import load_run, open_database, show_task_state
from supervisor.storage import RunStore


def test_reports_cli_loads_a_run_from_read_only_database(tmp_path: Path):
    database = tmp_path / "supervisor.sqlite3"
    result = WorkerResult(status=Status.PASS, summary="Done", recommended_next_step=NextStep.COMPLETE)
    event = RunEvent(stage="qwen", agent="Qwen3 Coder", model="test", attempt=1, status=Status.PASS, summary="Done", route="test", result=result)
    store = RunStore(database)
    store.save(TaskRun(run_id="run-42", task=Task(task_id="D006", title="Time"), status=Status.PASS, route="accepted", worker_results=[result], events=[event]))
    store.close()
    connection = open_database(database)
    try:
        assert load_run(connection, "run-42")["task"]["task_id"] == "D006"
    finally:
        connection.close()


def test_reports_cli_shows_durable_task_state(tmp_path: Path, capsys):
    database = tmp_path / "supervisor.sqlite3"
    store = RunStore(database)
    store.claim_task("D006", "run-42", 12345)
    store.checkpoint(
        "run-42", "D006", "qwen", "Qwen3 Coder", "heartbeat",
        {"summary": "Agent last requested: write_file.", "next_action": "continue_from_last_agent_tool", "changed_files": ["lib/time.dart"]},
    )
    store.close()
    connection = open_database(database)
    try:
        show_task_state(connection, "D006", 5)
    finally:
        connection.close()
    output = capsys.readouterr().out
    assert "D006 durable task state" in output
    assert "Agent last requested: write_file." in output
