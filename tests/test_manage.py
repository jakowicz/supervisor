from pathlib import Path

from supervisor import manage


def test_initialise_project_scaffolds_a_safe_empty_project(monkeypatch, tmp_path: Path):
    commands: list[tuple[list[str], Path | None]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        commands.append((command, cwd))
        if command[:3] == ["git", "submodule", "add"]:
            supervisor_root = cwd / "supervisor"
            supervisor_root.mkdir()
            (supervisor_root / ".env.example").write_text("SUPERVISOR_REPO_ROOT=..\n", encoding="utf-8")

    monkeypatch.setattr(manage, "_run", fake_run)
    project = tmp_path / "new-project"

    manage.initialise_project(project, supervisor_url="https://example.test/supervisor.git", python="python3.11", install=False, observability=False)

    assert commands == [
        (["git", "init"], project),
        (["git", "submodule", "add", "https://example.test/supervisor.git", "supervisor"], project),
    ]
    assert (project / ".gitignore").read_text(encoding="utf-8") == "/.state/\n"
    assert (project / "runbooks" / "TEMPLATE.md").read_text(encoding="utf-8") == manage.RUNBOOK_TEMPLATE
    assert (project / "supervisor" / ".env").read_text(encoding="utf-8") == "SUPERVISOR_REPO_ROOT=..\n"


def test_initialise_project_rejects_non_empty_directory(tmp_path: Path):
    project = tmp_path / "occupied"
    project.mkdir()
    (project / "existing.txt").write_text("keep", encoding="utf-8")

    try:
        manage.initialise_project(project, install=False, observability=False)
    except ValueError as error:
        assert "not empty" in str(error)
    else:
        raise AssertionError("Expected non-empty project directory to be rejected.")


def test_setup_local_langfuse_reuses_a_running_instance(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(manage, "local_langfuse_running", lambda: True)
    monkeypatch.setattr(manage, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not bootstrap")))

    manage.setup_local_langfuse(tmp_path)

    assert "Reusing the shared local Langfuse instance" in capsys.readouterr().out


def test_setup_local_langfuse_bootstraps_only_when_absent(monkeypatch, tmp_path: Path):
    script = tmp_path / "observability" / "setup-local.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "local_langfuse_running", lambda: False)
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))

    manage.setup_local_langfuse(tmp_path)

    assert commands == [(["bash", str(script)], tmp_path)]
