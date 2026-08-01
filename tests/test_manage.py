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


def test_choose_python_prefers_a_compatible_interpreter_on_path(monkeypatch):
    monkeypatch.setattr(manage.shutil, "which", lambda command: {"python3.11": "/tools/python3.11"}.get(command))

    class Process:
        returncode = 0
        stdout = "3.11\n"

    monkeypatch.setattr(manage.subprocess, "run", lambda *_args, **_kwargs: Process())

    assert manage.choose_python() == "/tools/python3.11"


def test_choose_python_rejects_an_incompatible_explicit_override(monkeypatch):
    monkeypatch.setattr(manage.shutil, "which", lambda _command: "/tools/python3")

    class Process:
        returncode = 0
        stdout = "3.9\n"

    monkeypatch.setattr(manage.subprocess, "run", lambda *_args, **_kwargs: Process())

    try:
        manage.choose_python("python3")
    except RuntimeError as error:
        assert "not an available Python" in str(error)
    else:
        raise AssertionError("Expected an incompatible Python override to be rejected.")


def test_configure_has_safe_bundled_worker_command_defaults():
    assert manage.DEFAULTS["QWEN_CODER_COMMAND"] == "./.venv/bin/python scripts/qwen_worker.py {task_file}"
    assert manage.DEFAULTS["OPENHANDS_COMMAND"] == "./.venv/bin/python scripts/openhands_worker.py {task_file}"
    assert manage.DEFAULTS["CODEX_COMMAND"] == "./.venv/bin/python scripts/codex_worker.py {task_file}"
    assert manage.DEFAULTS["BROWSER_QA_COMMAND"] == "./.venv/bin/python scripts/browser_worker.py {task_file}"
    assert manage.DEFAULTS["SUPERVISOR_ALLOW_AUTONOMOUS_WRITES"] == "true"
    assert manage.DEFAULTS["SUPERVISOR_AUTO_COMMIT"] == "true"
    assert manage.DEFAULTS["SUPERVISOR_AUTO_PUSH"] == "true"
    assert manage.DEFAULTS["LLM_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert manage.DEFAULTS["CODEX_MODEL"] == "gpt-5.6-terra"


def test_ollama_models_parses_installed_names(monkeypatch):
    class Process:
        returncode = 0
        stdout = "NAME ID SIZE MODIFIED\nqwen3-coder-next:latest abc 58 GB now\ngemma4:12b def 8 GB now\n"

    monkeypatch.setattr(manage.subprocess, "run", lambda *_args, **_kwargs: Process())

    assert manage.ollama_models() == ["qwen3-coder-next:latest", "gemma4:12b"]


def test_model_choices_prefer_qwen_and_terra(monkeypatch):
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert manage._choose_ollama_model("Qwen", "", ["gemma4:12b", "qwen3-coder-next:latest"]) == "qwen3-coder-next:latest"
    assert manage._choose_codex_model("") == "gpt-5.6-terra"


def test_project_path_prompt_requires_a_path(monkeypatch, tmp_path: Path, capsys):
    answers = iter(["", str(tmp_path / "new-project")])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = manage._project_path_prompt()

    assert result == (tmp_path / "new-project").resolve()
    assert "A project directory is required." in capsys.readouterr().out


def test_update_workspace_fast_forwards_and_reinstalls(monkeypatch, tmp_path: Path):
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(tmp_path) if command[1] == "rev-parse" else "")
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))
    monkeypatch.setattr(manage.sys, "executable", "/tools/python")

    result = manage.update_workspace(tmp_path)

    assert result == tmp_path
    assert commands == [
        (["git", "pull", "--ff-only", "origin", "main"], tmp_path),
        (["/tools/python", "-m", "pip", "install", "-e", ".[dev]"], tmp_path),
    ]


def test_update_workspace_refuses_tracked_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(tmp_path) if command[1] == "rev-parse" else " M README.md")

    try:
        manage.update_workspace(tmp_path)
    except RuntimeError as error:
        assert "local changes" in str(error)
    else:
        raise AssertionError("Expected a dirty Supervisor checkout to be rejected.")


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


def test_setup_local_langfuse_passes_initial_account_values(monkeypatch, tmp_path: Path):
    script = tmp_path / "observability" / "setup-local.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "local_langfuse_running", lambda: False)
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))

    manage.setup_local_langfuse(tmp_path, email="me@example.com", name="Me", password="safe-password")

    assert commands == [(["bash", str(script), "--email", "me@example.com", "--name", "Me", "--password", "safe-password"], tmp_path)]
