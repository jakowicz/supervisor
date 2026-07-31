from pathlib import Path

from supervisor.git_publish import preflight, publish
from supervisor.models import Status, Task


def test_preflight_is_inert_when_auto_publish_is_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SUPERVISOR_AUTO_COMMIT", raising=False)
    result = preflight(Task(task_id="T01", title="Test"), tmp_path)
    assert result.status is Status.PASS


def test_preflight_refuses_a_dirty_worktree(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SUPERVISOR_AUTO_COMMIT", "true")

    class Process:
        returncode = 0
        stdout = " M app.dart\\n"
        stderr = ""

    monkeypatch.setattr("supervisor.git_publish._run", lambda *_: Process())
    result = preflight(Task(task_id="T01", title="Test"), tmp_path)
    assert result.status is Status.NEEDS_USER_REVIEW


def test_publish_accepts_a_fully_validated_task_when_auto_commit_is_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SUPERVISOR_AUTO_COMMIT", raising=False)

    result = publish(Task(task_id="M002", title="Mock"), tmp_path)

    assert result.status is Status.PASS
    assert "no Git commit was created" in result.summary
