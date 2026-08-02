"""Interactive project configuration for the reusable supervisor."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import dotenv_values


DEFAULTS = {
    # The intended installation is a submodule at the project-root `supervisor`.
    # This resolves to the containing project while still remaining editable.
    "SUPERVISOR_REPO_ROOT": "..",
    # Operational evidence belongs to the controlled project's root, not the
    # reusable submodule. This keeps a supervisor upgrade from owning or
    # obscuring a project's execution history.
    "SUPERVISOR_DATABASE_PATH": "../.state/supervisor.sqlite3",
    "SUPERVISOR_QWEN_ATTEMPTS": "1",
    "SUPERVISOR_OPENHANDS_ATTEMPTS": "1",
    "SUPERVISOR_CODEX_ATTEMPTS": "3",
    "SUPERVISOR_CODEX_FINAL_ATTEMPTS": "3",
    "SUPERVISOR_DASHBOARD_PORT": "8765",
    "SUPERVISOR_WORKER_TIMEOUT_SECONDS": "1800",
    "SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS": "600",
    "SUPERVISOR_PROGRESS_HEARTBEAT_SECONDS": "30",
    "SUPERVISOR_ALLOW_AUTONOMOUS_WRITES": "true",
    "SUPERVISOR_AUTO_COMMIT": "true",
    "SUPERVISOR_AUTO_PUSH": "true",
    "SUPERVISOR_OBSERVABILITY_ENABLED": "true",
    "SUPERVISOR_OBSERVABILITY_ENVIRONMENT": "local",
    "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
    "LLM_BASE_URL": "http://127.0.0.1:11434/v1",
    "CODEX_MODEL": "gpt-5.6-terra",
    "SUPERVISOR_CODING_AGENTS": "qwen,openhands,codex",
    "SUPERVISOR_AGENT_ORDER": "",
    # These bundled adapters are safe defaults: they still need their
    # respective CLI/provider installed, and editing remains disabled until
    # the user explicitly enables SUPERVISOR_ALLOW_AUTONOMOUS_WRITES.
    "QWEN_CODER_COMMAND": "./.venv/bin/python scripts/qwen_worker.py {task_file}",
    "OPENHANDS_COMMAND": "./.venv/bin/python scripts/openhands_worker.py {task_file}",
    "CODEX_COMMAND": "./.venv/bin/python scripts/codex_worker.py {task_file}",
    "BROWSER_QA_COMMAND": "./.venv/bin/python scripts/browser_worker.py {task_file}",
    "VISUAL_REVIEW_COMMAND": "./.venv/bin/python scripts/visual_review_worker.py {task_file}",
}

DEFAULT_SUPERVISOR_URL = "git@github.com:jakowicz/supervisor.git"
DEFAULT_INITIAL_BRIEF_PATH = Path(__file__).resolve().parents[2] / "runbooks" / "INITIAL.md"
MINIMUM_PYTHON_VERSION = (3, 10)
CODEX_MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")
GAME_TEST_COMMANDS = (
    "flutter analyze --no-fatal-infos",
    "flutter test",
    "flutter build web --release",
)
GAME_TEST_COMMANDS_JSON = json.dumps(GAME_TEST_COMMANDS, separators=(",", ":"))
ENV_MIGRATION_MANIFEST_PATH = Path(__file__).with_name("env_migrations.json")


def _env_migration_manifest() -> dict[str, object]:
    """Load the Supervisor-owned, committed environment migration history."""

    try:
        manifest = json.loads(ENV_MIGRATION_MANIFEST_PATH.read_text(encoding="utf-8"))
        if not isinstance(manifest.get("schema_version"), int) or not isinstance(manifest.get("migrations"), list):
            raise ValueError("schema_version and migrations are required")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"Invalid Supervisor environment migration manifest: {ENV_MIGRATION_MANIFEST_PATH}: {error}") from error
    return manifest


ENV_MIGRATION_MANIFEST = _env_migration_manifest()
ENV_SCHEMA_VERSION = ENV_MIGRATION_MANIFEST["schema_version"]
ENV_MIGRATIONS = {migration["version"]: migration for migration in ENV_MIGRATION_MANIFEST["migrations"]}
PROJECT_TYPE_DEFAULTS = {
    "game": {
        "SUPERVISOR_CODING_AGENTS": "codex",
        "SUPERVISOR_AGENT_ORDER": "codex,test,browser,visual_review,completion_audit,git_publish",
        "SUPERVISOR_TEST_COMMANDS": GAME_TEST_COMMANDS_JSON,
    },
    "documents": {
        "SUPERVISOR_CODING_AGENTS": "codex",
        "SUPERVISOR_AGENT_ORDER": "codex",
    },
}
PRODUCT_CATEGORIES = (
    "Consumer application",
    "Business / internal application",
    "Game",
    "Document, planning, or content system",
    "Operating-system or device utility",
    "Developer tool or platform",
    "Service, API, or background system",
    "Other",
)
TARGET_SYSTEMS = (
    "Android phone",
    "Android tablet / ChromeOS",
    "iPhone (iOS)",
    "iPad (iPadOS)",
    "Wear OS",
    "watchOS",
    "Desktop web application",
    "Embedded web / WebView surface",
    "Browser extension",
    "macOS",
    "Windows",
    "Linux",
    "Apple TV / tvOS",
    "Android TV / Google TV",
    "Amazon Fire TV",
    "Samsung Smart TV / Tizen",
    "LG Smart TV / webOS",
    "Roku",
    "Hisense / VIDAA",
    "PlayStation",
    "Xbox",
    "Nintendo Switch",
    "PC game storefronts",
    "Meta Quest / virtual reality",
    "Augmented or mixed reality",
    "Kiosk, point-of-sale, or dedicated hardware",
    "Automotive display / Android Automotive / CarPlay",
    "Voice assistant or conversational interface",
    "Backend API",
    "Background workers / scheduled jobs",
    "Admin or operations portal",
    "Third-party partner API / SDK",
    "Data import, export, or migration tool",
)
DEFAULT_WEB_TARGETS = ("Responsive public web application", "Progressive web app (PWA)")

RUNBOOK_TEMPLATE = """---
task_id: T001
sequence: 1
title: Describe one small, reviewable change
browser_impact: none
playwright_spec:
---

## Objective

Describe the smallest complete outcome. State what is in scope and, equally
important, what must not be changed.

## Acceptance criteria

- The intended behaviour is implemented and documented where it affects users or developers.
- Focused automated tests cover the change and pass.
- No unrelated refactors, network calls, or credentials are introduced.
"""

RUNBOOKS_README = """# Runbooks

Put one Markdown runbook here for each small, independently reviewable task.
Start with `TEMPLATE.md`, assign a unique task ID and sequence, and be precise
about acceptance criteria. Then run it from `../supervisor`:

```zsh
./.venv/bin/supervisor-run --runbook ../runbooks/T001.md
```
"""


def _prompt(label: str, default: str, *, secret: bool = False) -> str:
    prompt = (
        f"{label} (press Enter to keep the current value): "
        if secret and default
        else f"{label} [{default}]: " if default else f"{label}: "
    )
    value = getpass.getpass(prompt) if secret else input(prompt)
    return value.strip() or default


def _yes_no(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]: ").strip().lower()
    return default if not value else value in {"y", "yes"}


def _project_type_prompt(default: str = "documents") -> str:
    """Choose the starter execution profile without preventing later edits."""

    while True:
        value = input("Project type [game/documents] [documents]: ").strip().lower() or default
        if value in PROJECT_TYPE_DEFAULTS:
            return value
        print("Choose 'game' or 'documents'.")


def _required(label: str) -> str:
    while not (value := input(f"{label}: ").strip()):
        print("This field is required.")
    return value


def _multiline(label: str) -> str:
    print(f"{label} (enter one item per line; enter a single '.' when finished):")
    lines: list[str] = []
    while (line := input("> ").strip()) != ".":
        if line:
            lines.append(line)
    return "\n".join(f"- {line}" for line in lines) or "- None recorded."


def _choose_one(label: str, options: tuple[str, ...]) -> str:
    print(f"\n{label}:")
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    while True:
        value = input("Choose one number: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        print("Enter one of the listed numbers.")


def _choose_many(label: str, options: tuple[str, ...]) -> list[str]:
    print(f"\n{label} (comma-separated numbers; blank for no additional targets):")
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    while True:
        value = input("Choose numbers: ").strip()
        if not value:
            return []
        try:
            selected = [int(item.strip()) for item in value.split(",")]
        except ValueError:
            selected = []
        if selected and all(1 <= item <= len(options) for item in selected):
            return [options[item - 1] for item in dict.fromkeys(selected)]
        print("Enter valid comma-separated numbers, or leave blank.")


def _render_initial_brief(values: dict[str, str], targets: list[str], target_details: dict[str, str]) -> str:
    target_lines = "\n".join(f"- [x] {target}" for target in targets)
    detail_lines = "\n".join(f"### {target}\n\n{detail}" for target, detail in target_details.items())
    return f"""# Initial project brief

This file is the source of truth for the document-producing collection. Later
runbooks must preserve its scope and record unanswered questions rather than
inventing requirements.

## What are we creating?

{values['product']}

## Product category

- [x] {values['category']}

## Who is it for, and what must it help them do?

- Intended users: {values['users']}
- Their primary outcome: {values['primary_outcome']}
- What makes the first useful session successful: {values['first_session']}

## Required first-release capabilities

{values['capabilities']}

## Later or deferred capabilities

{values['deferred']}

## Target systems and delivery surfaces

Responsive public web application and Progressive web app (PWA) are included by
default. Additional selected targets:

{target_lines}

## Per-target requirements

{detail_lines}

## Constraints and non-goals

- Technology and repository constraints: {values['technology']}
- Privacy, security, accessibility, offline, integration, cost, and delivery constraints: {values['constraints']}
- Explicitly excluded work: {values['non_goals']}

## Cross-platform product decisions

- Shared versus platform-specific capabilities: {values['parity']}
- Data synchronisation, offline, and conflict policy: {values['sync']}
- Accessibility, localisation, privacy, parental-control, store, or certification requirements: {values['compliance']}
- Minimum supported OS, browser, device class, and network condition: {values['support']}

## Reference boundaries

{values['references']}

## Open decisions

{values['open_decisions']}
"""


def create_initial_brief(path: Path, *, force: bool = False) -> Path:
    """Interactively collect a complete project brief and write INITIAL.md."""

    path = path.expanduser().resolve()
    if path.exists() and not force:
        raise ValueError(f"Initial brief already exists: {path}. Use --force to replace it.")
    category = _choose_one("What type of product are you building", PRODUCT_CATEGORIES)
    additional_targets = _choose_many("Select any additional target systems", TARGET_SYSTEMS)
    targets = [*DEFAULT_WEB_TARGETS, *additional_targets]
    target_details = {
        target: _required(f"Requirements for {target} (input, screens, offline, performance, release constraints)")
        for target in targets
    }
    values = {
        "product": _required("Describe what you are creating"),
        "category": category,
        "users": _required("Who is it for"),
        "primary_outcome": _required("What is their primary outcome"),
        "first_session": _required("What makes the first useful session successful"),
        "capabilities": _multiline("Required first-release capabilities"),
        "deferred": _multiline("Later or deferred capabilities"),
        "technology": _required("Technology and repository constraints"),
        "constraints": _required("Privacy, security, accessibility, offline, integration, cost, and delivery constraints"),
        "non_goals": _required("Explicitly excluded work"),
        "parity": _required("Which capabilities are shared versus platform-specific"),
        "sync": _required("Data synchronisation, offline, and conflict policy"),
        "compliance": _required("Accessibility, localisation, privacy, store, or certification requirements"),
        "support": _required("Minimum supported OS, browser, device class, and network condition"),
        "references": _required("Functional references and their no-copy boundaries"),
        "open_decisions": _multiline("Open decisions requiring approval"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_initial_brief(values, targets, target_details), encoding="utf-8")
    return path


def ollama_models() -> list[str]:
    """Return locally installed Ollama model names without failing setup."""

    try:
        result = subprocess.run(["ollama", "list"], text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    models: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] not in models:
            models.append(fields[0])
    return models


def _normalise_ollama_model(value: str) -> str:
    return value.removeprefix("ollama/")


def _choose_ollama_model(label: str, current: str, models: list[str]) -> str:
    """Offer local Ollama models, preserving a current explicit value."""

    current = _normalise_ollama_model(current)
    if not models:
        print("No Ollama models were detected. Enter a model name, or leave blank to use the provider default.")
        return _prompt(f"{label} model", current)
    default = current if current in models else next((model for model in models if "qwen" in model.lower()), models[0])
    print(f"\nInstalled Ollama models for {label}:")
    for index, model in enumerate(models, start=1):
        marker = " (default)" if model == default else ""
        print(f"  {index}. {model}{marker}")
    print("  0. Enter a different model name")
    while True:
        choice = input(f"Choose {label} model [{models.index(default) + 1}]: ").strip()
        if not choice:
            return default
        if choice == "0":
            return _prompt(f"{label} model", default)
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        if choice in models:
            return choice
        print(f"Choose 0-{len(models)}, or enter one of the listed model names.")


def _choose_codex_model(current: str) -> str:
    default = current if current else DEFAULTS["CODEX_MODEL"]
    print("\nCodex model:")
    for index, model in enumerate(CODEX_MODELS, start=1):
        marker = " (default)" if model == default else ""
        print(f"  {index}. {model}{marker}")
    print("  0. Enter a different model name")
    while True:
        choice = input(f"Choose Codex model [{CODEX_MODELS.index(default) + 1 if default in CODEX_MODELS else 1}]: ").strip()
        if not choice:
            return default
        if choice == "0":
            return _prompt("Codex model", default)
        if choice.isdigit() and 1 <= int(choice) <= len(CODEX_MODELS):
            return CODEX_MODELS[int(choice) - 1]
        if choice in CODEX_MODELS:
            return choice
        print(f"Choose 0-{len(CODEX_MODELS)}, or enter one of the listed model names.")


def _project_path_prompt(default: Path | None = None) -> Path:
    """Prompt for the only required project-init value before other setup."""

    shown_default = str(default) if default else ""
    while True:
        value = _prompt("Project directory", shown_default)
        if value:
            return Path(value).expanduser().resolve()
        print("A project directory is required.")


def _value(values: dict[str, str | None], key: str) -> str:
    return str(values.get(key) or os.getenv(key) or DEFAULTS.get(key, ""))


def _credentials_from(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    loaded = dotenv_values(path)
    public = loaded.get("LANGFUSE_PUBLIC_KEY") or loaded.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY")
    secret = loaded.get("LANGFUSE_SECRET_KEY") or loaded.get("LANGFUSE_INIT_PROJECT_SECRET_KEY")
    if not public or not secret:
        return {}
    return {
        "LANGFUSE_BASE_URL": str(loaded.get("LANGFUSE_BASE_URL") or "http://127.0.0.1:3001"),
        "LANGFUSE_PUBLIC_KEY": str(public),
        "LANGFUSE_SECRET_KEY": str(secret),
    }


def shared_langfuse_credentials(supervisor_root: Path) -> dict[str, str]:
    """Read managed local-project keys without displaying any secret."""

    config_root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    for candidate in (
        config_root / "runbook-supervisor" / "langfuse.env",
        supervisor_root / "observability" / ".env",
    ):
        credentials = _credentials_from(candidate)
        if credentials:
            return credentials
    return {}


def prompt_initial_langfuse_account() -> dict[str, str]:
    """Collect only the account values needed for a brand-new local service."""

    print("Supervisor will install/start the shared local Langfuse service.")
    print("Choose its initial administrator account (the password may be left blank to generate one securely):")
    return {
        "email": _prompt("Initial Langfuse username (email)", "local@supervisor.invalid"),
        "name": _prompt("Initial Langfuse display name", "Local Operator"),
        "password": _prompt("Initial Langfuse password", "", secret=True),
    }


def configure_langfuse(path: Path, values: dict[str, str]) -> None:
    """Use/start shared local Langfuse, falling back to an explicit key prompt."""

    supervisor_root = path.parent
    if not local_langfuse_running():
        if _yes_no("Local Langfuse is not running. Install/start the shared local service now", True):
            setup_local_langfuse(supervisor_root, **prompt_initial_langfuse_account())
        else:
            print("Langfuse was not started. Enter credentials for an existing remote or local project.")
    if not values.get("LANGFUSE_PUBLIC_KEY") or not values.get("LANGFUSE_SECRET_KEY"):
        discovered = shared_langfuse_credentials(supervisor_root) if local_langfuse_running() else {}
        if discovered:
            values.update(discovered)
            print("Reusing the shared local Langfuse default-project credentials.")
        else:
            print("\nLangfuse project credentials could not be discovered. Enter them below:")
            values["LANGFUSE_PUBLIC_KEY"] = _prompt("Langfuse public key", "", secret=True)
            values["LANGFUSE_SECRET_KEY"] = _prompt("Langfuse secret key", "", secret=True)


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by `supervisor configure`. Keep this file out of Git.",
        "# Re-run the command whenever the agent, QA, dashboard, or telemetry policy changes.",
        "",
    ]
    for key in sorted(values):
        value = values[key]
        if value:
            lines.append(f"{key}={value}")
    payload = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    path.chmod(0o600)


def migrate_env(path: Path, *, project_root: Path | None = None) -> list[str]:
    """Apply append-only environment migrations without overwriting user values.

    The source of truth is the committed `supervisor/env_migrations.json` in
    the updated Supervisor checkout, not a project runtime log or
    `.env.example`. Each project keeps only its applied schema version and its
    own values; existing values are never overwritten.
    """

    path = path.expanduser().resolve()
    project_root = (project_root or path.parent.parent).expanduser().resolve()
    existing = dotenv_values(path) if path.is_file() else {}
    raw_version = existing.get("SUPERVISOR_ENV_SCHEMA_VERSION") or "0"
    try:
        current_version = int(raw_version)
    except ValueError as error:
        raise ValueError(f"Invalid SUPERVISOR_ENV_SCHEMA_VERSION in {path}: {raw_version!r}") from error
    if current_version > ENV_SCHEMA_VERSION:
        raise ValueError(
            f"{path} uses environment schema {current_version}, newer than this Supervisor supports ({ENV_SCHEMA_VERSION})."
        )

    lines: list[str] = []
    changes: list[str] = []
    for version in range(current_version + 1, ENV_SCHEMA_VERSION + 1):
        migration = ENV_MIGRATIONS[version]
        for old_key, new_key in migration["rename"].items():
            old_value = existing.get(old_key)
            if old_value is not None and existing.get(new_key) is None:
                lines.append(f"{new_key}={old_value}")
                existing[new_key] = old_value
                changes.append(f"v{version} renamed {old_key} to {new_key}")
        for key, value in migration["add"].items():
            if existing.get(key) is None:
                lines.append(f"{key}={value}")
                existing[key] = value
                changes.append(f"v{version} added {key}")
        for addition in migration.get("conditional_add", []):
            if addition["when"] == "flutter_project" and (project_root / "pubspec.yaml").is_file():
                key, value = addition["key"], addition["value"]
                if existing.get(key) is None:
                    lines.append(f"{key}={value}")
                    existing[key] = value
                    changes.append(f"v{version} added {key} after detecting pubspec.yaml")
            elif addition["when"] == "flutter_project" and addition.get("otherwise"):
                changes.append(f"v{version} {addition['otherwise']}")
        changes.append(f"v{version} recorded {migration['description']}")

    if current_version != ENV_SCHEMA_VERSION:
        lines.append(f"SUPERVISOR_ENV_SCHEMA_VERSION={ENV_SCHEMA_VERSION}")
    if not lines:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not path.is_file() or not path.read_text(encoding="utf-8").strip() else "\n"
    with path.open("a", encoding="utf-8") as output:
        output.write(prefix + "# Applied by `supervisor update`; values already set above were preserved.\n")
        output.write("\n".join(lines) + "\n")
    path.chmod(0o600)

    return changes


def configure(path: Path) -> None:
    existing = dotenv_values(path) if path.is_file() else {}
    values = {key: str(value) for key, value in existing.items() if value is not None}
    print("Configure the project-local supervisor. Press Enter to keep a shown value.")
    # A supervisor checkout is always installed at <project>/supervisor, so
    # the application root is deterministically its parent. Preserve an
    # existing override without making every setup answer this same question.
    values["SUPERVISOR_REPO_ROOT"] = _value(existing, "SUPERVISOR_REPO_ROOT")
    values["SUPERVISOR_DATABASE_PATH"] = _prompt("SQLite database path", _value(existing, "SUPERVISOR_DATABASE_PATH"))
    values["SUPERVISOR_DASHBOARD_PORT"] = _prompt("Preferred dashboard port (a free port is selected if busy)", _value(existing, "SUPERVISOR_DASHBOARD_PORT"))
    values["SUPERVISOR_WORKER_TIMEOUT_SECONDS"] = _prompt("Worker timeout in seconds", _value(existing, "SUPERVISOR_WORKER_TIMEOUT_SECONDS"))
    values["SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS"] = _prompt("Qwen no-progress timeout in seconds", _value(existing, "SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS"))
    values["SUPERVISOR_PROGRESS_HEARTBEAT_SECONDS"] = _prompt("Terminal heartbeat in seconds", _value(existing, "SUPERVISOR_PROGRESS_HEARTBEAT_SECONDS"))
    legacy_test_command = _value(existing, "SUPERVISOR_TEST_COMMAND")
    test_commands_default = _value(existing, "SUPERVISOR_TEST_COMMANDS") or (
        json.dumps([legacy_test_command], separators=(",", ":")) if legacy_test_command else ""
    )
    values["SUPERVISOR_TEST_COMMANDS"] = _prompt(
        "Validation commands as a JSON array (for example [\"npm test\",\"npm run build\"])",
        test_commands_default,
    )
    values["SUPERVISOR_CODING_AGENTS"] = _prompt("Coding agents in execution order (for example qwen,openhands,codex)", _value(existing, "SUPERVISOR_CODING_AGENTS"))
    # Pipeline gates are intentionally fixed. Agent selection and retry counts
    # remain configurable, but no interactive setting may skip independent QA.
    values.pop("SUPERVISOR_AGENT_ORDER", None)

    print("\nAgent retry policy (total implementation attempts; Codex final repairs are additional after its first review):")
    for key, label in (
        ("SUPERVISOR_QWEN_ATTEMPTS", "Qwen attempts"),
        ("SUPERVISOR_OPENHANDS_ATTEMPTS", "OpenHands attempts"),
        ("SUPERVISOR_CODEX_ATTEMPTS", "Codex fallback attempts"),
        ("SUPERVISOR_CODEX_FINAL_ATTEMPTS", "Codex final-review repairs after QA failure"),
    ):
        values[key] = _prompt(label, _value(existing, key))

    values["SUPERVISOR_ALLOW_AUTONOMOUS_WRITES"] = "true" if _yes_no("Allow coding agents to edit this project", _value(existing, "SUPERVISOR_ALLOW_AUTONOMOUS_WRITES") == "true") else "false"
    publish = _yes_no(
        "Automatically commit and push accepted tasks",
        _value(existing, "SUPERVISOR_AUTO_COMMIT") == "true" and _value(existing, "SUPERVISOR_AUTO_PUSH") == "true",
    )
    values["SUPERVISOR_AUTO_COMMIT"] = "true" if publish else "false"
    values["SUPERVISOR_AUTO_PUSH"] = "true" if publish else "false"

    # Bundled adapters and shared-local telemetry need no repeated setup
    # choices. Keep any project-specific visual reviewer unchanged.
    values.pop("SUPERVISOR_TEST_COMMAND", None)
    for key in ("QWEN_CODER_COMMAND", "OPENHANDS_COMMAND", "CODEX_COMMAND", "BROWSER_QA_COMMAND", "VISUAL_REVIEW_COMMAND"):
        values[key] = _value(existing, key)
    models = ollama_models()
    values["QWEN_MODEL"] = _choose_ollama_model("Qwen", _value(existing, "QWEN_MODEL"), models)
    openhands_model = _choose_ollama_model("OpenHands", _value(existing, "LLM_MODEL"), models)
    values["LLM_MODEL"] = f"ollama/{openhands_model}" if openhands_model else ""
    values["LLM_BASE_URL"] = _value(existing, "LLM_BASE_URL")
    values["CODEX_MODEL"] = _choose_codex_model(_value(existing, "CODEX_MODEL"))

    values["SUPERVISOR_OBSERVABILITY_ENABLED"] = "true"
    values["SUPERVISOR_OBSERVABILITY_ENVIRONMENT"] = _value(existing, "SUPERVISOR_OBSERVABILITY_ENVIRONMENT")
    values["LANGFUSE_BASE_URL"] = _value(existing, "LANGFUSE_BASE_URL")
    configure_langfuse(path, values)

    _write_env(path, values)
    print(f"Wrote {path} (mode 600). Secrets were not printed.")
    print("Next: run `supervisor-run --task-id <ID>` or `supervisor-dashboard --serve`.")


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"Required command was not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        rendered = " ".join(command)
        raise RuntimeError(f"Command failed ({error.returncode}): {rendered}") from error


def _git_output(command: list[str], *, cwd: Path) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("The Supervisor checkout is not a usable Git repository.") from error
    return result.stdout.strip()


def project_supervisor_checkout(start: Path) -> Path:
    """Find the nearest project-owned `supervisor/` checkout from a cwd."""

    current = start.expanduser().resolve()
    candidates = [current] if current.name == "supervisor" else []
    candidates.extend(parent / "supervisor" for parent in (current, *current.parents))
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / ".git").exists():
            return candidate
    raise RuntimeError(
        f"No project Supervisor checkout was found from {current}. Run this command from a project containing supervisor/."
    )


def update_workspace(start: Path | None = None) -> Path:
    """Fast-forward the current project's Supervisor checkout and refresh it."""

    package_root = project_supervisor_checkout(start or Path.cwd())
    repository_root = Path(_git_output(["git", "rev-parse", "--show-toplevel"], cwd=package_root))
    if _git_output(["git", "status", "--porcelain"], cwd=repository_root):
        raise RuntimeError(
            f"Refusing to update a Supervisor checkout with local changes: {repository_root}. "
            "Commit, stash, or discard those changes first."
        )
    print(f"Updating this project's Supervisor checkout in {repository_root}")
    _run(["git", "pull", "--ff-only", "origin", "main"], cwd=repository_root)
    project_python = repository_root / ".venv" / "bin" / "python"
    python = str(project_python) if project_python.is_file() else sys.executable
    _run([python, "-m", "pip", "install", "-e", ".[dev]"], cwd=repository_root)
    _run(
        [python, "-m", "supervisor.manage", "env-migrate", "--config", str(repository_root / ".env"), "--project-root", str(repository_root.parent)],
        cwd=repository_root,
    )
    print("Project Supervisor tools and .env migrations are up to date. Commit the changed supervisor submodule pointer in the parent project when ready.")
    return repository_root


def upgrade_cli(start: Path | None = None) -> Path:
    """Update the checkout that provides the currently invoked Supervisor CLI."""

    package_root = (start or Path(__file__).resolve().parents[1]).resolve()
    repository_root = Path(_git_output(["git", "rev-parse", "--show-toplevel"], cwd=package_root))
    if _git_output(["git", "status", "--porcelain"], cwd=repository_root):
        raise RuntimeError(
            f"Refusing to upgrade a Supervisor checkout with local changes: {repository_root}. "
            "Commit, stash, or discard those changes first."
        )
    print(f"Upgrading the Supervisor CLI from {repository_root}")
    _run(["git", "pull", "--ff-only", "origin", "main"], cwd=repository_root)
    _run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], cwd=repository_root)
    print("Supervisor CLI tools are up to date.")
    return repository_root


def choose_python(requested: str | None = None) -> str:
    """Find a Python 3.10+ interpreter on PATH, or validate an override."""

    candidates = [requested] if requested else [
        "python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3", "python",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if not resolved:
            continue
        probe = subprocess.run(
            [resolved, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            continue
        try:
            major, minor = (int(part) for part in probe.stdout.strip().split(".", maxsplit=1))
        except ValueError:
            continue
        if (major, minor) >= MINIMUM_PYTHON_VERSION:
            return resolved
    if requested:
        raise RuntimeError(f"{requested!r} is not an available Python {MINIMUM_PYTHON_VERSION[0]}.{MINIMUM_PYTHON_VERSION[1]}+ interpreter.")
    raise RuntimeError("No Python 3.10+ interpreter was found on PATH. Install one or pass --python <path>.")


def _append_gitignore(path: Path, entry: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if entry in {line.strip() for line in existing.splitlines()}:
        return
    newline = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{newline}{entry}\n", encoding="utf-8")


def local_langfuse_running() -> bool:
    """Return whether the shared local Langfuse endpoint is reachable."""

    try:
        with urlopen("http://127.0.0.1:3001/", timeout=2) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def setup_local_langfuse(
    supervisor_root: Path,
    *,
    email: str | None = None,
    name: str | None = None,
    password: str | None = None,
) -> None:
    """Start one local Langfuse instance only when a shared one is absent."""

    if local_langfuse_running():
        print("Reusing the shared local Langfuse instance at http://127.0.0.1:3001.")
        return
    script = supervisor_root / "observability" / "setup-local.sh"
    if not script.is_file():
        raise RuntimeError(f"Local Langfuse bootstrap script is missing: {script}")
    print("No local Langfuse instance is running; starting the shared local setup.")
    command = ["bash", str(script)]
    if email:
        command.extend(["--email", email])
    if name:
        command.extend(["--name", name])
    if password:
        command.extend(["--password", password])
    _run(command, cwd=supervisor_root)


def initialise_project(
    project_root: Path,
    *,
    supervisor_url: str = DEFAULT_SUPERVISOR_URL,
    python: str | None = None,
    install: bool = True,
    observability: bool = True,
    project_type: str = "documents",
    langfuse_email: str | None = None,
    langfuse_name: str | None = None,
    langfuse_password: str | None = None,
) -> None:
    """Create an empty Git project ready to run scoped supervisor runbooks."""

    project_root = project_root.expanduser().resolve()
    if project_root.exists() and any(project_root.iterdir()):
        raise ValueError(f"Project directory is not empty: {project_root}")
    if project_type not in PROJECT_TYPE_DEFAULTS:
        raise ValueError(f"Unknown project type: {project_type}")
    project_root.mkdir(parents=True, exist_ok=True)

    print(f"Initialising Git project: {project_root}")
    _run(["git", "init"], cwd=project_root)
    print(f"Adding supervisor submodule: {supervisor_url}")
    _run(["git", "submodule", "add", supervisor_url, "supervisor"], cwd=project_root)

    runbooks = project_root / "runbooks"
    runbooks.mkdir(exist_ok=True)
    (runbooks / "README.md").write_text(RUNBOOKS_README, encoding="utf-8")
    (runbooks / "TEMPLATE.md").write_text(RUNBOOK_TEMPLATE, encoding="utf-8")
    _append_gitignore(project_root / ".gitignore", "/.state/")

    example = project_root / "supervisor" / ".env.example"
    config = project_root / "supervisor" / ".env"
    if example.is_file() and not config.exists():
        config.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        config.chmod(0o600)
    if config.exists():
        with config.open("a", encoding="utf-8") as output:
            output.write("\n# Project-type starter profile; change during `supervisor configure` if needed.\n")
            for key, value in PROJECT_TYPE_DEFAULTS[project_type].items():
                output.write(f"{key}={value}\n")

    if install:
        venv = project_root / "supervisor" / ".venv"
        selected_python = choose_python(python)
        print(f"Creating virtual environment with {selected_python}")
        _run([selected_python, "-m", "venv", str(venv)], cwd=project_root)
        venv_python = venv / "bin" / "python"
        print("Installing supervisor dependencies")
        _run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], cwd=project_root / "supervisor")

    if observability:
        setup_local_langfuse(
            project_root / "supervisor",
            email=langfuse_email,
            name=langfuse_name,
            password=langfuse_password,
        )

    print("Project ready.")
    print(f"1. Edit {runbooks / 'TEMPLATE.md'} and save it as your first task runbook.")
    print(f"2. Run: cd {project_root / 'supervisor'} && ./.venv/bin/supervisor configure")
    print("3. Set explicit worker commands, Langfuse project keys, and write permissions before running a task.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create, configure, update, and maintain an evidence-gated Supervisor project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Common workflows:
  supervisor init ../my-project
      Create an empty project with a Supervisor checkout and starter runbooks.
  supervisor initial --force
      Interactively write runbooks/INITIAL.md for a document-producing factory.
  supervisor configure
      Review the local .env, including coding agents and stage order.
  supervisor update
      Fast-forward this project's supervisor/ checkout from origin/main.
  supervisor upgrade
      Update the checkout that installed the currently invoked Supervisor CLI.

Use `supervisor <command> --help` for that command's options. After setup, use
supervisor-run to execute runbooks, supervisor-reports to inspect evidence, and
supervisor-dashboard --serve to view the local dashboard.""",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    configure_parser = commands.add_parser("configure", help="Interactively create or update an ignored .env configuration file.")
    configure_parser.add_argument("--config", type=Path, default=Path(".env"), help="Configuration file to write (default: .env).")
    initial_parser = commands.add_parser("initial", help="Interactively create the project brief consumed by the first runbook.")
    initial_parser.add_argument("--output", type=Path, default=DEFAULT_INITIAL_BRIEF_PATH, help="Brief path (default: <project>/runbooks/INITIAL.md).")
    initial_parser.add_argument("--force", action="store_true", help="Replace an existing initial brief after collecting new answers.")
    migration_parser = commands.add_parser("env-migrate", help="Apply versioned, non-destructive .env migrations and record an audit log.")
    migration_parser.add_argument("--config", type=Path, default=Path(".env"), help="Project .env path (default: .env).")
    migration_parser.add_argument("--project-root", type=Path, help="Project root for .state/supervisor-env-migrations.log.")
    init_parser = commands.add_parser("init", help="Interactively create and configure an empty Git project with the supervisor, runbooks, and shared Langfuse setup.")
    init_parser.add_argument("project", type=Path, nargs="?", help="Optional new or empty project directory; prompted for when omitted.")
    init_parser.add_argument("--supervisor-url", default=DEFAULT_SUPERVISOR_URL, help="Git URL for the supervisor submodule.")
    init_parser.add_argument("--python", help="Optional Python 3.10+ interpreter override; by default the best compatible interpreter on PATH is selected.")
    init_parser.add_argument("--no-install", action="store_true", help="Create files and submodule but skip virtualenv and dependency installation.")
    init_parser.add_argument("--no-observability", action="store_true", help="Do not reuse or start the shared local Langfuse setup.")
    init_parser.add_argument("--non-interactive", action="store_true", help="Use defaults and skip all setup prompts; intended for automation.")
    init_parser.add_argument("--project-type", choices=tuple(PROJECT_TYPE_DEFAULTS), help="Starter pipeline profile; prompted for during interactive setup.")
    commands.add_parser("update", help="Fast-forward the current project's supervisor/ checkout from origin/main and reinstall its CLI tools.")
    commands.add_parser("upgrade", help="Fast-forward the checkout that provides this Supervisor CLI and reinstall its commands.")
    if len(sys.argv) == 1:
        parser.print_help()
        return
    arguments = parser.parse_args()
    if arguments.command == "configure":
        configure(arguments.config.expanduser().resolve())
    if arguments.command == "initial":
        try:
            brief_path = create_initial_brief(arguments.output, force=arguments.force)
        except ValueError as error:
            parser.error(str(error))
        print(f"Wrote initial project brief: {brief_path}")
    if arguments.command == "env-migrate":
        try:
            changes = migrate_env(arguments.config, project_root=arguments.project_root)
        except ValueError as error:
            parser.error(str(error))
        if changes:
            print(f"Updated {arguments.config}:")
            for change in changes:
                print(f"- {change}")
        else:
            print(f"{arguments.config} already uses Supervisor environment schema {ENV_SCHEMA_VERSION}; no changes needed.")
    if arguments.command == "update":
        try:
            update_workspace(Path.cwd())
        except RuntimeError as error:
            parser.error(str(error))
    if arguments.command == "upgrade":
        try:
            upgrade_cli()
        except RuntimeError as error:
            parser.error(str(error))
    if arguments.command == "init":
        try:
            if arguments.non_interactive and arguments.project is None:
                parser.error("project is required with --non-interactive")
            project_path = (
                arguments.project.expanduser().resolve()
                if arguments.non_interactive
                else _project_path_prompt(arguments.project.expanduser().resolve() if arguments.project else None)
            )
            project_type = (
                arguments.project_type or "documents"
                if arguments.non_interactive
                else arguments.project_type or _project_type_prompt()
            )
            if project_path.exists() and any(project_path.iterdir()):
                parser.error(f"Project directory is not empty: {project_path}")
            observability = not arguments.no_observability
            bootstrap_account: dict[str, str | None] = {}
            if observability and not arguments.non_interactive and not local_langfuse_running():
                print("\nNo shared local Langfuse instance is running.")
                if _yes_no("Start shared local Langfuse now", True):
                    account = prompt_initial_langfuse_account()
                    bootstrap_account = {
                        "langfuse_email": account["email"],
                        "langfuse_name": account["name"],
                        "langfuse_password": account["password"],
                    }
                else:
                    observability = False
            initialise_project(
                project_path,
                supervisor_url=arguments.supervisor_url,
                python=arguments.python,
                install=not arguments.no_install,
                observability=observability,
                project_type=project_type,
                **bootstrap_account,
            )
            if not arguments.non_interactive:
                print("\nConfigure this project's supervisor:")
                configure(project_path / "supervisor" / ".env")
        except (RuntimeError, ValueError) as error:
            parser.error(str(error))


if __name__ == "__main__":
    main()
