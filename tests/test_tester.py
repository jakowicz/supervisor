from types import SimpleNamespace

from supervisor.models import Status, Task
from supervisor.workers.tester import run


def test_tester_uses_a_configured_non_flutter_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("SUPERVISOR_TEST_COMMAND", "node --test tests")
    monkeypatch.setattr("supervisor.workers.tester.subprocess.run", fake_run)
    result = run(Task(task_id="M001", title="Mock"), tmp_path)
    assert calls == [["node", "--test", "tests"]]
    assert result.status is Status.PASS
    assert "Configured project checks" in result.summary
