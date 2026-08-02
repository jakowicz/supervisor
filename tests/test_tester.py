from types import SimpleNamespace

from supervisor.models import Status, Task
from supervisor.workers.tester import run


def test_tester_uses_an_ordered_project_validation_contract(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("SUPERVISOR_TEST_COMMANDS", '["node --test tests", "npm run build"]')
    monkeypatch.setattr("supervisor.workers.tester.subprocess.run", fake_run)
    result = run(Task(task_id="M001", title="Mock"), tmp_path)
    assert calls == [["node", "--test", "tests"], ["npm", "run", "build"]]
    assert result.status is Status.PASS
    assert "Configured project checks" in result.summary


def test_tester_requires_a_project_validation_contract(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERVISOR_TEST_COMMAND", raising=False)
    monkeypatch.delenv("SUPERVISOR_TEST_COMMANDS", raising=False)

    result = run(Task(task_id="D010", title="Release journey"), tmp_path)

    assert result.status is Status.ENVIRONMENT_FAILURE
    assert "SUPERVISOR_TEST_COMMANDS" in result.summary
