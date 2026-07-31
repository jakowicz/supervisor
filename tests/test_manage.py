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

    manage.initialise_project(project, supervisor_url="https://example.test/supervisor.git", python="python3.11", install=False)

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
        manage.initialise_project(project, install=False)
    except ValueError as error:
        assert "not empty" in str(error)
    else:
        raise AssertionError("Expected non-empty project directory to be rejected.")
