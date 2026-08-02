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


def test_tester_attaches_golden_diff_artifacts_to_a_failed_check(monkeypatch, tmp_path):
    failures = tmp_path / "test" / "golden" / "failures"
    failures.mkdir(parents=True)
    for suffix in ("masterImage", "testImage", "maskedDiff"):
        (failures / f"settings_standard_default_{suffix}.png").write_bytes(b"png")

    monkeypatch.setenv("SUPERVISOR_TEST_COMMANDS", '["flutter test"]')
    monkeypatch.setattr(
        "supervisor.workers.tester.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="golden mismatch", stderr=""),
    )

    result = run(Task(task_id="D012", title="Typography"), tmp_path)

    assert result.status is Status.REPAIRABLE_FAILURE
    assert "Golden visual diff artifacts" in result.test_result
    assert "settings_standard_default_masterImage.png" in result.test_result
    assert len(result.evidence.screenshots) == 3
