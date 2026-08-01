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


def test_default_tester_runs_an_available_project_contract_check(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.delenv("SUPERVISOR_TEST_COMMAND", raising=False)
    contract = tmp_path / "scripts" / "check_release_qa_docs.sh"
    contract.parent.mkdir()
    contract.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    contract.chmod(0o755)
    monkeypatch.setattr("supervisor.workers.tester.subprocess.run", fake_run)

    result = run(Task(task_id="D010", title="Release journey"), tmp_path)

    assert calls[:3] == [
        ["flutter", "analyze", "--no-fatal-infos"],
        ["flutter", "test"],
        ["flutter", "build", "web", "--release"],
    ]
    assert calls[-1] == ["scripts/check_release_qa_docs.sh"]
    assert result.status is Status.PASS
    assert "project-contract checks" in result.summary
