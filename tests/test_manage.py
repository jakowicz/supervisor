import sys
import re
from pathlib import Path

from supervisor import manage


def test_interactive_init_asks_for_project_type_immediately_after_project_path(monkeypatch, tmp_path: Path):
    prompts: list[str] = []
    project = tmp_path / "new-project"

    monkeypatch.setattr(sys, "argv", ["supervisor", "init", "--no-install", "--no-observability"])
    monkeypatch.setattr(manage, "_project_path_prompt", lambda _requested: prompts.append("project_path") or project)
    monkeypatch.setattr(manage, "_project_type_prompt", lambda: prompts.append("project_type") or "game")
    monkeypatch.setattr(manage, "initialise_project", lambda *_args, **kwargs: prompts.append(f"initialise:{kwargs['project_type']}"))
    monkeypatch.setattr(manage, "configure", lambda _path: prompts.append("configure"))

    manage.main()

    assert prompts == ["project_path", "project_type", "initialise:game", "configure"]


def test_initial_brief_renderer_always_includes_responsive_web_and_pwa():
    values = {
        "product": "A task app",
        "category": "Consumer application",
        "users": "People",
        "primary_outcome": "Organise work",
        "first_session": "A saved task",
        "capabilities": "- Capture tasks",
        "deferred": "- Team workspaces",
        "technology": "No constraint",
        "constraints": "Accessible",
        "non_goals": "No billing",
        "parity": "Shared core",
        "sync": "Offline first",
        "compliance": "WCAG",
        "support": "Modern browsers",
        "references": "Capabilities only; no copied expression.",
        "open_decisions": "- Branding",
    }

    rendered = manage._render_initial_brief(
        values,
        [*manage.DEFAULT_WEB_TARGETS, "iPhone (iOS)"],
        {"Responsive public web application": "Keyboard and touch.", "Progressive web app (PWA)": "Installable.", "iPhone (iOS)": "Touch."},
    )

    assert "- [x] Responsive public web application" in rendered
    assert "- [x] Progressive web app (PWA)" in rendered
    assert "### iPhone (iOS)" in rendered


def test_choose_many_fallback_accepts_multiple_target_systems(monkeypatch):
    answers = iter(["1, 3, 2, 3"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    selected = manage._choose_many_fallback("Select target systems", ("Android", "iPhone", "Windows"))

    assert selected == ["Android", "Windows", "iPhone"]


def test_game_target_family_only_offers_compatible_game_targets(monkeypatch):
    monkeypatch.setattr(manage, "_choose_one", lambda _label, options: options[2])

    family, targets = manage._choose_target_family("Game")

    assert family == "Console game"
    assert targets == ("PlayStation", "Xbox", "Nintendo Switch", "PC game storefronts")


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
    config = (project / "supervisor" / ".env").read_text(encoding="utf-8")
    assert "SUPERVISOR_REPO_ROOT=.." in config
    assert "SUPERVISOR_CODING_AGENTS=codex" in config
    assert "SUPERVISOR_AGENT_ORDER=codex" in config


def test_initialise_project_applies_the_game_pipeline_profile(monkeypatch, tmp_path: Path):
    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        if command[:3] == ["git", "submodule", "add"]:
            (cwd / "supervisor").mkdir()
            (cwd / "supervisor" / ".env.example").write_text("", encoding="utf-8")

    monkeypatch.setattr(manage, "_run", fake_run)
    project = tmp_path / "game-project"

    manage.initialise_project(project, install=False, observability=False, project_type="game")

    config = (project / "supervisor" / ".env").read_text(encoding="utf-8")
    assert "SUPERVISOR_CODING_AGENTS=codex" in config
    assert "SUPERVISOR_AGENT_ORDER=codex,test,browser,visual_review,completion_audit,git_publish" in config
    assert f"SUPERVISOR_TEST_COMMANDS={manage.GAME_TEST_COMMANDS_JSON}" in config


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


def test_shared_langfuse_credentials_reads_bootstrap_key_pair(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    supervisor_root = tmp_path / "supervisor"
    observability = supervisor_root / "observability"
    observability.mkdir(parents=True)
    (observability / ".env").write_text(
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-test\nLANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-test\n",
        encoding="utf-8",
    )

    assert manage.shared_langfuse_credentials(supervisor_root) == {
        "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
    }


def test_configure_langfuse_starts_then_reuses_discovered_local_project(monkeypatch, tmp_path: Path):
    calls: list[Path] = []
    running = iter([False, True])
    monkeypatch.setattr(manage, "local_langfuse_running", lambda: next(running))
    monkeypatch.setattr(manage, "_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(manage, "prompt_initial_langfuse_account", lambda: {"email": "admin@example.com", "name": "Admin", "password": "password"})
    monkeypatch.setattr(manage, "setup_local_langfuse", lambda root, **account: calls.append((root, account)))
    monkeypatch.setattr(
        manage,
        "shared_langfuse_credentials",
        lambda _root: {"LANGFUSE_BASE_URL": "http://127.0.0.1:3001", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
    )
    values: dict[str, str] = {}
    config = tmp_path / "supervisor" / ".env"

    manage.configure_langfuse(config, values)

    assert calls == [(config.parent, {"email": "admin@example.com", "name": "Admin", "password": "password"})]
    assert values["LANGFUSE_PUBLIC_KEY"] == "pk"
    assert values["LANGFUSE_SECRET_KEY"] == "sk"


def test_project_path_prompt_requires_a_path(monkeypatch, tmp_path: Path, capsys):
    answers = iter(["", str(tmp_path / "new-project")])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = manage._project_path_prompt()

    assert result == (tmp_path / "new-project").resolve()
    assert "A project directory is required." in capsys.readouterr().out


def test_update_workspace_fast_forwards_and_reinstalls(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "supervisor"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(checkout) if command[1] == "rev-parse" else "")
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))
    monkeypatch.setattr(manage.sys, "executable", "/tools/python")

    result = manage.update_workspace(tmp_path)

    assert result == checkout
    assert commands == [
        (["git", "pull", "--ff-only", "origin", "main"], checkout),
        (["/tools/python", "-m", "pip", "install", "-e", ".[dev]"], checkout),
        (["/tools/python", "-m", "supervisor.manage", "env-migrate", "--config", str(checkout / ".env"), "--project-root", str(tmp_path)], checkout),
    ]


def test_migrate_env_adds_missing_keys_preserves_values_and_uses_committed_manifest(tmp_path: Path):
    config = tmp_path / "supervisor" / ".env"
    config.parent.mkdir()
    config.write_text("CODEX_MODEL=project-choice\n", encoding="utf-8")

    changes = manage.migrate_env(config, project_root=tmp_path)

    migrated = config.read_text(encoding="utf-8")
    assert "CODEX_MODEL=project-choice" in migrated
    assert f"SUPERVISOR_ENV_SCHEMA_VERSION={manage.ENV_SCHEMA_VERSION}" in migrated
    assert "SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS=600" in migrated
    assert any("added SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS" in change for change in changes)
    assert manage.ENV_MIGRATION_MANIFEST_PATH.is_file()
    assert not (tmp_path / ".state" / "supervisor-env-migrations.log").exists()
    assert manage.migrate_env(config, project_root=tmp_path) == []


def test_migrate_env_seeds_flutter_validation_only_for_flutter_projects(tmp_path: Path):
    config = tmp_path / "supervisor" / ".env"
    config.parent.mkdir()
    (tmp_path / "pubspec.yaml").write_text("name: game\n", encoding="utf-8")

    manage.migrate_env(config, project_root=tmp_path)

    assert f"SUPERVISOR_TEST_COMMANDS={manage.GAME_TEST_COMMANDS_JSON}" in config.read_text(encoding="utf-8")


def test_env_example_documents_the_current_migration_schema_and_every_migration_key():
    example = (manage.ENV_MIGRATION_MANIFEST_PATH.parent.parent / ".env.example").read_text(encoding="utf-8")

    assert re.search(rf"^SUPERVISOR_ENV_SCHEMA_VERSION={manage.ENV_SCHEMA_VERSION}$", example, re.MULTILINE)
    for migration in manage.ENV_MIGRATIONS.values():
        keys = [*migration["add"], *migration["rename"].values()]
        keys.extend(addition["key"] for addition in migration.get("conditional_add", []))
        for key in keys:
            assert re.search(rf"^#?\s*{re.escape(key)}=", example, re.MULTILINE), (
                f".env.example must document {key} from Supervisor migration {migration['version']}"
            )


def test_update_workspace_refuses_tracked_changes(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "supervisor"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(checkout) if command[1] == "rev-parse" else " M README.md")

    try:
        manage.update_workspace(tmp_path)
    except RuntimeError as error:
        assert "local changes" in str(error)
    else:
        raise AssertionError("Expected a dirty Supervisor checkout to be rejected.")


def test_project_supervisor_checkout_rejects_unrelated_directory(tmp_path: Path):
    try:
        manage.project_supervisor_checkout(tmp_path)
    except RuntimeError as error:
        assert "No project Supervisor checkout" in str(error)
    else:
        raise AssertionError("Expected a directory without supervisor/ to be rejected.")


def test_upgrade_cli_updates_the_checkout_providing_the_command(monkeypatch, tmp_path: Path):
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(tmp_path) if command[1] == "rev-parse" else "")
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))
    monkeypatch.setattr(manage.sys, "executable", "/tools/python")

    assert manage.upgrade_cli(tmp_path) == tmp_path
    assert commands == [
        (["git", "pull", "--ff-only", "origin", "main"], tmp_path),
        (["/tools/python", "-m", "pip", "install", "-e", ".[dev]"], tmp_path),
    ]


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
