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
        "project_name": "Task App",
        "project_slug": "task-app",
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
        "art_direction": "- No custom direction supplied.",
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
    assert "- Workspace: `projects/task-app/`" in rendered
    assert "## Art direction" in rendered


def test_initial_art_direction_updates_only_art_values_in_project_env(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "supervisor"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    config = tmp_path / "projects" / "moonlit" / ".env"
    config.parent.mkdir(parents=True)
    config.write_text("LANGFUSE_SECRET_KEY=secret\nART_STYLE_NAME=old\n", encoding="utf-8")
    recorded = manage._record_initial_art_direction(config.parent, "moonlit", "cool moonlit science-fiction, clean geometric shapes")

    updated = config.read_text(encoding="utf-8")
    assert "LANGFUSE_SECRET_KEY=secret" in updated
    assert "ART_DIRECTION_MODE=user_provided" in updated
    assert "ART_DIRECTION_BRIEF=cool moonlit science-fiction, clean geometric shapes" in updated
    assert "ART_PRODUCT_SLUG=moonlit" in updated
    assert "The project `.env` has been updated" in recorded


def test_blank_initial_art_direction_configures_gemma4(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "supervisor"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    workspace = tmp_path / "projects" / "moonlit"
    recorded = manage._record_initial_art_direction(workspace, "moonlit", "")

    updated = (workspace / ".env").read_text(encoding="utf-8")
    assert "ART_DIRECTION_MODE=gemma4_auto" in updated
    assert "ART_DIRECTION_MODEL=gemma4:12b" in updated
    assert "Gemma 4 12B will create" in recorded


def test_preparing_named_project_workspace_owns_env_and_state(tmp_path: Path):
    workspace = tmp_path / "projects" / "moonlit"

    manage._prepare_project_workspace(workspace)

    assert (workspace / ".state").is_dir()
    env = (workspace / ".env").read_text(encoding="utf-8")
    assert "SUPERVISOR_REPO_ROOT=.." in env
    assert "SUPERVISOR_DATABASE_PATH=.state/supervisor.sqlite3" in env


def test_project_slug_is_safe_for_a_workspace_path():
    assert manage._project_slug("Final Fantasy V: Reborn!") == "final-fantasy-v-reborn"


def test_interactive_examples_are_coloured_and_follow_the_question(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "A task app")

    assert manage._required("Describe the product", example="A thoughtful task manager.") == "A task app"

    output = capsys.readouterr().out
    assert output.index("Describe the product:") < output.index(manage.EXAMPLE_COLOUR)
    assert f"{manage.EXAMPLE_COLOUR}Example: A thoughtful task manager.{manage.TERMINAL_RESET}" in output


def test_project_statuses_reports_progress_and_resume_command_inputs(tmp_path: Path):
    projects = tmp_path / "projects"
    workspace = projects / "task-app"
    workspace.mkdir(parents=True)
    (workspace / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    factory = tmp_path / "runbooks"
    factory.mkdir()
    (factory / "F001.md").write_text(
        "---\ntask_id: F001\nsequence: 1\ntitle: Factory\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nDo it.\n\n## Acceptance criteria\n\n- Done.\n",
        encoding="utf-8",
    )

    status = manage.project_statuses(projects, factory)[0]

    assert status["name"] == "task-app"
    assert status["phase"] == "F-series factory"
    assert status["next"] == "F001"


def test_choose_many_fallback_accepts_multiple_target_systems(monkeypatch):
    answers = iter(["1, 3, 2, 3"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    selected = manage._choose_many_fallback("Select target systems", ("Android", "iPhone", "Windows"))

    assert selected == ["Android", "Windows", "iPhone"]


def test_target_selector_infers_a_compatible_game_family(monkeypatch):
    monkeypatch.setattr(manage, "_choose_one", lambda *_args: "Console game")
    monkeypatch.setattr(manage, "_choose_many", lambda *_args: ["PlayStation", "Xbox"])

    family, targets = manage._choose_compatible_targets("Game")

    assert family == "Console game"
    assert targets == ["PlayStation", "Xbox"]


def test_game_target_requirements_are_platform_specific_and_shared_requirements_include_rpg_needs():
    options = manage._game_target_requirement_options("PlayStation", ["Role-playing game (RPG)"])
    shared = manage._game_shared_requirement_options(["Role-playing game (RPG)"])

    assert "Controller-first input, system navigation, and player profile handling" in options
    assert "Long-session save, checkpoint, and recovery rules" not in options
    assert "Long-session save, checkpoint, and recovery rules" in shared
    assert "Touch controls and orientation rules" not in options


def test_multiplayer_games_infer_social_features_and_deferred_content_is_clear():
    options = manage._game_shared_requirement_options(["Multiplayer game"])

    assert "Social features, communities, and player-safety requirements" in options
    assert "Online multiplayer" not in manage.GAME_DEFERRED_CAPABILITIES
    assert "Additional platforms" not in manage.GAME_DEFERRED_CAPABILITIES
    assert any("custom levels, mods, designs, or stories" in item for item in manage.GAME_DEFERRED_CAPABILITIES)


def test_game_target_requirements_are_derived_without_an_extra_prompt():
    requirements = manage._game_target_requirements("Windows", ["Real-time action, platformer, or combat game"])
    shared = manage._game_shared_requirement_options(["Real-time action, platformer, or combat game"])

    assert "- Keyboard, mouse, and gamepad support" in requirements
    assert "- Stable frame-time and input-latency target" not in requirements
    assert "Stable frame-time and input-latency target" in shared


def test_non_game_profiles_provide_guided_choices_and_inferred_requirements():
    profile = manage.APPLICATION_BRIEF_PROFILES["Developer tool or platform"]
    web_requirements = manage._application_target_requirement_options("Responsive public web application")
    shared_requirements = manage._application_shared_requirements("Developer tool or platform")

    assert "Individual developers" in profile["audiences"]
    assert "Build, test, debug, or ship software faster" in profile["outcomes"]
    assert "Responsive layout" in web_requirements
    assert "Accessibility, privacy, error recovery, and observability" in shared_requirements


def test_selected_bullets_combines_presets_and_optional_detail(monkeypatch):
    monkeypatch.setattr(manage, "_choose_many", lambda *_args: ["Accessible controls"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "Custom requirement")

    selected = manage._selected_bullets("Requirements", ("Accessible controls",), other_example="Example")

    assert selected == "- Accessible controls\n- Other: Custom requirement"


def test_selected_bullets_can_skip_the_optional_free_text_prompt(monkeypatch):
    monkeypatch.setattr(manage, "_choose_many", lambda *_args: ["Casual players"])
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(AssertionError("Unexpected prompt")))

    assert manage._selected_bullets("Player experience", ("Casual players",)) == "- Casual players"


def test_player_experience_options_are_mutually_exclusive():
    assert manage.GAME_PLAYER_EXPERIENCE == ("Casual players", "Core/hobby players")


def test_audience_group_options_are_mutually_exclusive():
    assert manage.GAME_AUDIENCE_GROUPS == ("Children and families", "Teen players", "Adult players", "Accessibility-first players")


def test_game_first_release_capabilities_are_baseline_requirements():
    capabilities = "\n".join(f"- {capability}" for capability in manage.GAME_FIRST_RELEASE_CAPABILITIES)

    assert "- Playable core loop" in capabilities
    assert "- Player account and identity" in capabilities
    assert "- Multiplayer services and player safety" in capabilities
    assert "- Live-service operations" in capabilities
    assert "- In-app purchases and entitlement handling" in capabilities
    assert "- Crash/error recovery" in capabilities


def test_game_cross_platform_delivery_defaults_to_shared_parallel_feature_parity():
    parity = "Build every selected platform in tandem from one shared core, with feature parity by default and platform-specific input or UI adaptations only where necessary."

    assert "in tandem" in parity
    assert "shared core" in parity
    assert "feature parity" in parity
    assert "Platform-specific companion experiences" not in manage.PLATFORM_STRATEGIES


def test_game_sync_defaults_to_remote_reusable_cross_device_state():
    sync = "Store player state remotely against the player account so it can be shared across selected devices wherever possible. Provide a local offline cache, safe synchronisation, and conflict recovery. Design the save-state service as a reusable platform capability rather than a game-specific silo."

    assert "remotely" in sync
    assert "across selected devices" in sync
    assert "reusable platform capability" in sync


def test_game_compliance_defaults_cover_baseline_and_platform_packages():
    compliance = "Provide localisation support, privacy consent and player data controls, and age-rating or parental-control requirements. Apply platform-appropriate accessibility requirements (including WCAG guidance for web surfaces). Build the appropriate distributable package for every selected platform."

    assert "localisation support" in compliance
    assert "privacy consent" in compliance
    assert "age-rating" in compliance
    assert "WCAG guidance for web surfaces" in compliance
    assert "distributable package for every selected platform" in compliance


def test_game_support_defaults_to_widely_used_devices_and_feasible_slow_network_use():
    support = "Support device classes and OS/browser versions that remain widely used for every selected platform. Make slow or offline network conditions usable wherever feasible without compromising the core game design or required online features."

    assert "widely used" in support
    assert "wherever feasible" in support


def test_initialise_project_scaffolds_a_safe_empty_project(monkeypatch, tmp_path: Path):
    commands: list[tuple[list[str], Path | None]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        commands.append((command, cwd))
        if command[:3] == ["git", "submodule", "add"]:
            supervisor_root = cwd / "supervisor"
            supervisor_root.mkdir()
            (supervisor_root / ".env.example").write_text("SUPERVISOR_REPO_ROOT=.\n", encoding="utf-8")

    monkeypatch.setattr(manage, "_run", fake_run)
    project = tmp_path / "new-project"

    manage.initialise_project(project, supervisor_url="https://example.test/supervisor.git", python="python3.11", install=False, observability=False)

    assert commands == [
        (["git", "init"], project),
        (["git", "submodule", "add", "https://example.test/supervisor.git", "supervisor"], project),
    ]
    assert (project / ".gitignore").read_text(encoding="utf-8") == "/.state/\n/.env\n"
    assert (project / "runbooks" / "TEMPLATE.md").read_text(encoding="utf-8") == manage.RUNBOOK_TEMPLATE
    config = (project / ".env").read_text(encoding="utf-8")
    assert "SUPERVISOR_REPO_ROOT=." in config
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

    config = (project / ".env").read_text(encoding="utf-8")
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
        (["/tools/python", "-m", "supervisor.manage", "env-migrate", "--config", str(tmp_path / ".env"), "--project-root", str(tmp_path)], checkout),
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


def test_update_workspace_ignores_untracked_generated_metadata(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "supervisor"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(manage, "_git_output", lambda command, *, cwd: str(checkout) if command[1] == "rev-parse" else "")
    monkeypatch.setattr(manage, "_run", lambda command, *, cwd=None: commands.append((command, cwd)))
    monkeypatch.setattr(manage.sys, "executable", "/tools/python")

    manage.update_workspace(tmp_path)

    assert commands[0] == (["git", "pull", "--ff-only", "origin", "main"], checkout)


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
