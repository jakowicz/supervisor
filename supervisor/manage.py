"""Interactive project configuration for the reusable supervisor."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import termios
import tty
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import dotenv_values

from .runbooks import load_task


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
    "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
    "COMFYUI_GENERATION_TIMEOUT_SECONDS": "600",
    "ASSET_GENERATOR_COMMAND": "./.venv/bin/python scripts/comfy_asset_generator.py {task_file}",
    "ASSET_FINISHER_COMMAND": "./.venv/bin/python scripts/asset_finisher.py {task_file}",
    "ASSET_QA_COMMAND": "./.venv/bin/python scripts/asset_qa_worker.py {task_file}",
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "LOCAL_VISION_MODEL": "gemma4:12b",
    "ASSET_VISION_QA_ENABLED": "false",
    "ASSET_VISION_TIMEOUT_SECONDS": "180",
    "ART_PRODUCT_SLUG": "project",
    "ART_STYLE_NAME": "original game-art style",
    "ART_STYLE_PROMPT": "original game asset, clear readable silhouette, premium hand-painted illustration, no text, no logo",
    "ART_NEGATIVE_PROMPT": "copied commercial game art, trademark, logo, watermark, text, UI screenshot, blurry, duplicate object",
    "ART_PROTECTED_IP_TERMS": "",
    "ART_DIRECTION_MODE": "gemma4_auto",
    "ART_DIRECTION_MODEL": "gemma4:12b",
    "ART_DIRECTION_BRIEF": "",
    "APP_TIME_OBSERVATION_KEY": "flutter.project.time_observation",
    "SUPERVISOR_FAILURE_SUMMARY_ENABLED": "true",
    "SUPERVISOR_FAILURE_SUMMARY_MODEL": "gemma4:12b",
    "SUPERVISOR_FAILURE_SUMMARY_TIMEOUT_SECONDS": "180",
    "SUPERVISOR_FAILURE_SUMMARY_KEEP_ALIVE": "0",
}

DEFAULT_SUPERVISOR_URL = "git@github.com:jakowicz/supervisor.git"
DEFAULT_PROJECTS_DIRECTORY = Path("projects")
MINIMUM_PYTHON_VERSION = (3, 10)
CODEX_MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")
GAME_TEST_COMMANDS = (
    "flutter analyze --no-fatal-infos",
    "flutter test",
    "flutter build web --release",
)
GAME_TEST_COMMANDS_JSON = json.dumps(GAME_TEST_COMMANDS, separators=(",", ":"))
ENV_MIGRATION_MANIFEST_PATH = Path(__file__).with_name("env_migrations.json")
EXAMPLE_COLOUR = "\033[36m"  # cyan: visible but readable in dark terminals
TERMINAL_RESET = "\033[0m"


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
    "PC game storefronts (Steam, Epic Games Store, GOG, itch.io)",
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
APPLICATION_TARGET_FAMILIES = (
    (
        "Connected application and companion apps",
        (
            "Android phone",
            "Android tablet / ChromeOS",
            "iPhone (iOS)",
            "iPad (iPadOS)",
            "Wear OS",
            "watchOS",
            "Desktop web application",
            "Browser extension",
            "macOS",
            "Windows",
            "Linux",
        ),
    ),
    ("Living-room TV application", ("Apple TV / tvOS", "Android TV / Google TV", "Amazon Fire TV", "Samsung Smart TV / Tizen", "LG Smart TV / webOS", "Roku", "Hisense / VIDAA")),
    ("Spatial application", ("Meta Quest / virtual reality", "Augmented or mixed reality")),
    ("Dedicated device or embedded application", ("Embedded web / WebView surface", "Kiosk, point-of-sale, or dedicated hardware", "Automotive display / Android Automotive / CarPlay", "Voice assistant or conversational interface")),
    ("Service and integration product", ("Backend API", "Background workers / scheduled jobs", "Admin or operations portal", "Third-party partner API / SDK", "Data import, export, or migration tool")),
)
GAME_TARGET_FAMILIES = (
    (
        "Cross-platform game (web, mobile, and desktop)",
        (
            "Android phone",
            "Android tablet / ChromeOS",
            "iPhone (iOS)",
            "iPad (iPadOS)",
            "Desktop web application",
            "macOS",
            "Windows",
            "Linux",
            "PC game storefronts (Steam, Epic Games Store, GOG, itch.io)",
        ),
    ),
    ("Living-room TV game", ("Apple TV / tvOS", "Android TV / Google TV", "Amazon Fire TV", "Samsung Smart TV / Tizen", "LG Smart TV / webOS", "Roku", "Hisense / VIDAA")),
    ("Console game", ("PlayStation", "Xbox", "Nintendo Switch", "PC game storefronts (Steam, Epic Games Store, GOG, itch.io)")),
    ("Spatial game", ("Meta Quest / virtual reality", "Augmented or mixed reality")),
)
GAME_PRESENTATIONS = (
    "2D presentation",
    "3D presentation",
    "Hybrid 2D and 3D presentation",
)
GAME_PLAYER_MODES = (
    "Single-player game",
    "Multiplayer game",
    "Single-player and multiplayer game",
)
GAME_GENRES = (
    "Role-playing game (RPG)",
    "Real-time action, platformer, or combat game",
    "Strategy, simulation, or management game",
    "Puzzle, card, board, or turn-based game",
    "Narrative or visual-novel game",
)
GAME_PLAYER_EXPERIENCE = ("Casual players", "Core/hobby players")
GAME_AUDIENCE_GROUPS = ("Children and families", "Teen players", "Adult players", "Accessibility-first players")
GAME_PRIMARY_OUTCOMES = ("Play a satisfying core game loop", "Progress through a story or campaign", "Compete with other players", "Create, collect, or customise", "Relax with short repeatable sessions")
GAME_FIRST_SESSION_SUCCESSES = ("Complete onboarding and play the core loop", "Finish a first level, battle, puzzle, or quest", "Create a character or save file", "Understand controls and choose accessibility settings")
GAME_FIRST_RELEASE_CAPABILITIES = ("Playable core loop", "Onboarding and tutorial", "Save/load and progression", "Settings and accessibility", "Audio and visual feedback", "Content/level delivery", "Player account and identity", "Multiplayer services and player safety", "Live-service operations", "In-app purchases and entitlement handling", "Crash/error recovery")
GAME_DEFERRED_CAPABILITIES = ("Live events or seasonal content", "Player-created and shared content (custom levels, mods, designs, or stories)", "Advanced analytics or monetisation")
COMMON_CONSTRAINTS = ("Accessibility support", "Privacy and data-minimisation", "Offline or unreliable-network support", "Performance and download-size budget", "Limited budget or delivery date", "Existing repository or technology constraint", "Third-party service integration")
TECHNOLOGY_CONSTRAINTS = ("Use an existing repository", "Use a specified framework or engine", "No paid infrastructure or services", "Must integrate with an existing API or backend", "No fixed technology constraint")
PLATFORM_STRATEGIES = ("Shared core with platform-specific input and UI", "One primary platform first; other selected platforms follow", "Feature parity across selected platforms")
SYNC_POLICIES = ("Local save only", "Cloud sync when signed in", "Offline-first with conflict resolution", "Online-only shared state")
COMMON_COMPLIANCE = ("WCAG-style accessibility", "Localisation support", "Privacy consent and data controls", "Age rating or parental controls", "Store certification and submission requirements")
SUPPORT_TARGETS = ("Latest supported OS/browser versions", "Current and previous major OS/browser versions", "Phone, tablet, desktop, and low-end device coverage", "Slow or offline network conditions")
APPLICATION_BRIEF_PROFILES = {
    "Consumer application": {
        "audiences": ("Individual consumers", "Families or households", "Creators or enthusiasts", "Accessibility-first users"),
        "outcomes": ("Complete a personal task quickly", "Track and improve a personal habit or goal", "Discover and consume useful content", "Create and share something useful"),
        "first_sessions": ("Complete the main action and see a useful result", "Create an account and personalise the experience", "Import or add their first piece of data", "Understand the core value without assistance"),
        "capabilities": ("Clear onboarding", "Core user workflow", "Account and cross-device data", "Settings and accessibility", "Error recovery and support"),
        "deferred": ("Advanced personalisation", "Social/community features", "Integrations", "Premium or monetisation features"),
    },
    "Business / internal application": {
        "audiences": ("Frontline operational staff", "Knowledge workers", "Managers and approvers", "Administrators"),
        "outcomes": ("Complete an operational workflow", "Find accurate business information", "Review and approve work", "Manage users, data, or configuration"),
        "first_sessions": ("Complete one real workflow with guidance", "Find a relevant record and take action", "Set up the workspace for a team", "Review a dashboard or queue"),
        "capabilities": ("Role-aware access", "Core business workflow", "Search and filtering", "Audit-friendly history", "Settings and accessibility"),
        "deferred": ("Advanced reporting", "External integrations", "Workflow automation", "Enterprise administration"),
    },
    "Document, planning, or content system": {
        "audiences": ("Individual planners", "Collaborative teams", "Writers or content creators", "Reviewers and approvers"),
        "outcomes": ("Create and organise useful content", "Plan and track work", "Collaborate on shared material", "Review and publish content"),
        "first_sessions": ("Create the first document, plan, or item", "Organise content into a useful structure", "Invite a collaborator and share work", "Publish or export a first result"),
        "capabilities": ("Create and edit content", "Organisation and search", "Version history", "Sharing and permissions", "Export and accessibility"),
        "deferred": ("Advanced templates", "Automation", "Third-party integrations", "Publishing workflows"),
    },
    "Operating-system or device utility": {
        "audiences": ("Everyday device users", "Power users", "IT/support staff", "Accessibility-first users"),
        "outcomes": ("Configure or repair a device capability", "Understand device status", "Automate a routine device task", "Keep data and settings safe"),
        "first_sessions": ("Complete setup safely", "See a clear device-status result", "Run the first useful utility action", "Understand permissions and recovery options"),
        "capabilities": ("Safe setup and permissions", "Core device workflow", "Status and diagnostics", "Recovery and error handling", "Accessibility"),
        "deferred": ("Automation", "Advanced diagnostics", "Enterprise controls", "Additional hardware support"),
    },
    "Developer tool or platform": {
        "audiences": ("Individual developers", "Engineering teams", "Platform engineers", "Open-source maintainers"),
        "outcomes": ("Build, test, debug, or ship software faster", "Understand a codebase or system", "Automate a development workflow", "Publish a reusable integration"),
        "first_sessions": ("Install or connect the tool and complete a useful action", "Run a first command, build, or analysis", "Open a sample project and see a result", "Create a first integration"),
        "capabilities": ("Installation and setup", "Core developer workflow", "Clear diagnostics", "Configuration and extensibility", "Documentation and examples"),
        "deferred": ("Plugin ecosystem", "Advanced automation", "Hosted collaboration", "Enterprise controls"),
    },
    "Service, API, or background system": {
        "audiences": ("Application developers", "Internal service owners", "Operations staff", "External integration partners"),
        "outcomes": ("Integrate a reliable capability", "Process work asynchronously", "Operate and monitor a service", "Move or transform data safely"),
        "first_sessions": ("Make a successful first API call or job run", "Connect a client and inspect a result", "Configure monitoring and see healthy status", "Import or process a first dataset"),
        "capabilities": ("Authentication and authorisation", "Core API or job workflow", "Validation and error contracts", "Observability and recovery", "Documentation and examples"),
        "deferred": ("Advanced rate controls", "Partner self-service", "Workflow automation", "Multi-region delivery"),
    },
    "Other": {
        "audiences": ("Individual users", "Teams", "Administrators", "Developers or operators"),
        "outcomes": ("Complete the main product workflow", "Find useful information", "Create or manage valuable data", "Collaborate or integrate with others"),
        "first_sessions": ("Complete the first core workflow", "Create or import the first useful item", "Understand the product value", "Configure the product safely"),
        "capabilities": ("Clear onboarding", "Core workflow", "Data and settings", "Error recovery", "Accessibility"),
        "deferred": ("Advanced workflows", "Integrations", "Automation", "Additional platforms"),
    },
}
PRODUCT_DESCRIPTION_EXAMPLES = {
    "Consumer application": "A personal task and habit app that helps individuals plan their day across phone and desktop.",
    "Business / internal application": "An internal operations system for support staff to triage, assign, and resolve customer requests.",
    "Game": "A turn-based fantasy role-playing game for short mobile and desktop sessions.",
    "Document, planning, or content system": "A collaborative workspace for teams to create plans, documents, and publishable knowledge.",
    "Operating-system or device utility": "A desktop utility that helps users understand, configure, and recover device storage safely.",
    "Developer tool or platform": "A developer tool that analyses a codebase, explains architecture, and helps engineers ship changes safely.",
    "Service, API, or background system": "An API and background processing service that receives files, validates them, and delivers reliable results to client applications.",
    "Other": "A focused product that helps its intended users complete one valuable workflow clearly and reliably.",
}
FUNCTIONAL_REFERENCE_EXAMPLES = {
    "Consumer application": "Todoist for task capture, projects, priorities, and recurring work.",
    "Business / internal application": "Zendesk for ticket queues, assignment, status, and operational workflows.",
    "Game": "Final Fantasy V for turn-based party combat and world progression.",
    "Document, planning, or content system": "Notion for structured pages, databases, collaboration, and publishing workflows.",
    "Operating-system or device utility": "macOS Disk Utility for clear status, safe actions, and recovery-oriented workflows.",
    "Developer tool or platform": "Visual Studio Code for editing, navigation, debugging, and extensions.",
    "Service, API, or background system": "Stripe for clear API contracts, developer documentation, reliable asynchronous processing, and operational visibility.",
    "Other": "A well-known product with a comparable user workflow and outcome.",
}

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


def _print_example(example: str, *, plural: bool = False) -> None:
    """Make optional guidance visually distinct from a required answer."""

    heading = "Examples" if plural else "Example"
    print(f"{EXAMPLE_COLOUR}{heading}: {example}{TERMINAL_RESET}")


def _prompt(label: str, default: str, *, secret: bool = False, example: str | None = None) -> str:
    if example:
        print()
        print(f"{label}:")
        _print_example(example)
        label = "Value"
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


def _required(label: str, *, example: str | None = None) -> str:
    print()
    print(f"{label}:")
    if example:
        _print_example(example)
    while not (value := input("> ").strip()):
        print("This field is required.")
    return value


def _multiline(label: str, *, example: str | None = None) -> str:
    print()
    print(f"{label} (enter one item per line; enter a single '.' when finished):")
    if example:
        _print_example(example, plural=True)
    lines: list[str] = []
    while (line := input("> ").strip()) != ".":
        if line:
            lines.append(line)
    return "\n".join(f"- {line}" for line in lines) or "- None recorded."


def _selected_bullets(label: str, options: tuple[str, ...], *, other_example: str | None = None) -> str:
    """Choose common answers first, then allow a concise product-specific note."""

    selected = _choose_many(label, options)
    other = ""
    if other_example:
        print()
        print("Optional other detail (press Enter to skip):")
        _print_example(other_example)
        other = input("> ").strip()
    lines = [f"- {item}" for item in selected]
    if other:
        lines.append(f"- Other: {other}")
    return "\n".join(lines) or "- None selected yet."


def _choose_one_with_example(label: str, options: tuple[str, ...], *, example: str) -> str:
    return _choose_one(label, options, example=example)


def _choose_one(label: str, options: tuple[str, ...], *, example: str | None = None) -> str:
    print(f"\n{label}:")
    if example:
        _print_example(example)
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    while True:
        value = input("Choose one number: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        print("Enter one of the listed numbers.")


def _choose_many_fallback(label: str, options: tuple[str, ...]) -> list[str]:
    """Non-interactive fallback for piped input and terminals without raw mode."""

    print(f"\n{label}:")
    print("Enter one or more numbers separated by commas.")
    _print_example("1, 4, 12")
    print("Press Enter when no additional targets are needed.")
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


def _choose_many(label: str, options: tuple[str, ...]) -> list[str]:
    """Choose one or more options with a keyboard checkbox selector.

    Raw terminal mode gives the normal interactive experience. The numeric
    fallback keeps the wizard usable from a redirected terminal and test runner.
    """

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _choose_many_fallback(label, options)

    selected: set[int] = set()
    current = 0
    line_count = len(options) + 3

    def render(*, replace: bool) -> None:
        if replace:
            sys.stdout.write(f"\x1b[{line_count}F\x1b[J")
        lines = [f"\n{label}", "Use ↑/↓ to move, Space to select, and Enter to confirm."]
        lines.extend(
            f"{'›' if index == current else ' '} [{'x' if index in selected else ' '}] {option}"
            for index, option in enumerate(options)
        )
        # Raw terminal mode disables the normal NL-to-CRLF conversion. Use an
        # explicit carriage return so each checkbox begins in the same column.
        sys.stdout.write("\r\n".join(lines) + "\r\n")
        sys.stdout.flush()

    stream = sys.stdin
    try:
        file_descriptor = stream.fileno()
    except (AttributeError, OSError):
        return _choose_many_fallback(label, options)

    original_settings = termios.tcgetattr(file_descriptor)
    try:
        tty.setraw(file_descriptor)
        sys.stdout.write("\x1b[?25l")
        render(replace=False)
        while True:
            key = stream.read(1)
            if key in {"\r", "\n"}:
                return [option for index, option in enumerate(options) if index in selected]
            if key == " ":
                if current in selected:
                    selected.remove(current)
                else:
                    selected.add(current)
                render(replace=True)
                continue
            if key == "\x1b":
                sequence = stream.read(2)
                if sequence == "[A":
                    current = (current - 1) % len(options)
                elif sequence == "[B":
                    current = (current + 1) % len(options)
                else:
                    continue
                render(replace=True)
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, original_settings)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _choose_compatible_targets(category: str) -> tuple[str, list[str]]:
    """Choose a delivery category, then select platforms inside that category."""

    families = GAME_TARGET_FAMILIES if category == "Game" else APPLICATION_TARGET_FAMILIES
    family_names = tuple(name for name, _targets in families)
    selected_family = _choose_one("Choose the delivery category", family_names)
    _name, targets = next(family for family in families if family[0] == selected_family)
    selected = _choose_many(f"Select every target for {selected_family}", targets)
    return selected_family, selected


def _game_target_requirement_options(target: str, characteristics: list[str]) -> tuple[str, ...]:
    """Return selectable delivery requirements appropriate to a game target."""

    options: list[str] = []
    if target in {"Responsive public web application", "Desktop web application"}:
        options.extend(("Keyboard, mouse, touch, and gamepad input", "Responsive layout across phone, tablet, and desktop screens", "Browser compatibility and web performance budget"))
    elif target == "Progressive web app (PWA)":
        options.extend(("Installable PWA experience", "Offline asset and save-data behaviour", "Safe game update and cache-refresh behaviour"))
    elif target in {"Android phone", "Android tablet / ChromeOS", "iPhone (iOS)", "iPad (iPadOS)"}:
        options.extend(("Touch controls and orientation rules", "Mobile performance, battery, thermal, and download-size budget", "App-store packaging, privacy disclosures, and release requirements"))
    elif target in {"macOS", "Windows", "Linux", "PC game storefronts (Steam, Epic Games Store, GOG, itch.io)"}:
        options.extend(("Keyboard, mouse, and gamepad support", "Window, display-resolution, graphics-quality, and accessibility settings", "Desktop packaging, installation, update, and storefront requirements"))
    elif target in {"PlayStation", "Xbox", "Nintendo Switch"}:
        options.extend(("Controller-first input, system navigation, and player profile handling", "Console save data, suspend/resume, achievement, and entitlement behaviour", "Performance targets and platform certification requirements"))
    elif "TV" in target or target in {"Samsung Smart TV / Tizen", "LG Smart TV / webOS", "Roku", "Hisense / VIDAA"}:
        options.extend(("Remote-control and game-controller navigation", "Ten-foot UI readability and safe viewing-area layout", "TV hardware performance and store-release requirements"))
    elif target in {"Meta Quest / virtual reality", "Augmented or mixed reality"}:
        options.extend(("Comfort settings, locomotion, and motion-sickness mitigation", "Tracked-controller, hand-input, and boundary behaviour", "Spatial performance, battery, and platform-store requirements"))

    return tuple(dict.fromkeys(options))


def _game_shared_requirement_options(characteristics: list[str]) -> tuple[str, ...]:
    """Return requirements that apply to the game regardless of target platform."""

    options = [
        "Accessible controls, text, captions, and visual settings",
        "Save, resume, and data-loss recovery behaviour",
        "Crash reporting and player-facing error recovery",
    ]
    if "Role-playing game (RPG)" in characteristics:
        options.extend(("Long-session save, checkpoint, and recovery rules", "Readable dialogue, inventory, quest, and progression UI"))
    if "Real-time action, platformer, or combat game" in characteristics:
        options.extend(("Stable frame-time and input-latency target", "Control remapping and difficulty/accessibility assists"))
    if "Multiplayer game" in characteristics or "Single-player and multiplayer game" in characteristics:
        options.extend(("Online identity, matchmaking, moderation, and reporting requirements", "Social features, communities, and player-safety requirements", "Network-loss, reconnection, and multiplayer state-recovery behaviour"))
    return tuple(dict.fromkeys(options))


def _application_target_requirement_options(target: str) -> tuple[str, ...]:
    """Derive target-specific delivery requirements for a non-game product."""

    if target in {"Responsive public web application", "Desktop web application"}:
        return ("Responsive layout", "Keyboard and pointer interaction", "Browser compatibility and web performance")
    if target == "Progressive web app (PWA)":
        return ("Installable PWA experience", "Offline cache and safe update behaviour")
    if target in {"Android phone", "Android tablet / ChromeOS", "iPhone (iOS)", "iPad (iPadOS)"}:
        return ("Touch interaction and orientation rules", "Mobile performance and download-size budget", "Mobile package and platform permission requirements")
    if target in {"macOS", "Windows", "Linux"}:
        return ("Desktop input, window, and display behaviour", "Desktop package, installation, and update behaviour")
    if "TV" in target or target in {"Samsung Smart TV / Tizen", "LG Smart TV / webOS", "Roku", "Hisense / VIDAA"}:
        return ("Remote-control navigation", "Ten-foot UI readability and safe viewing-area layout")
    if target in {"Meta Quest / virtual reality", "Augmented or mixed reality"}:
        return ("Spatial input and comfort requirements", "Spatial performance and boundary behaviour")
    return ("Target-appropriate packaging, input, performance, and release behaviour",)


def _application_shared_requirements(category: str) -> tuple[str, ...]:
    profile = APPLICATION_BRIEF_PROFILES[category]
    return tuple((*profile["capabilities"], "Cross-device state where applicable", "Accessibility, privacy, error recovery, and observability"))


def _game_target_requirements(target: str, characteristics: list[str]) -> str:
    """Derive baseline requirements from the selected game format and targets."""

    return "\n".join(f"- {requirement}" for requirement in _game_target_requirement_options(target, characteristics))


def _render_initial_brief(values: dict[str, str], targets: list[str], target_details: dict[str, str]) -> str:
    target_lines = "\n".join(f"- [x] {target}" for target in targets)
    detail_lines = "\n".join(f"### {target}\n\n{detail}" for target, detail in target_details.items())
    return f"""# Initial project brief

This file is the source of truth for the document-producing collection. Later
runbooks must preserve its scope and record unanswered questions rather than
inventing requirements.

## Project workspace

- Project name: {values['project_name']}
- Workspace: `projects/{values['project_slug']}/`

## What are we creating?

{values['product']}

## Product category

- [x] {values['category']}

## Game characteristics

{values.get('game_characteristics', '- Not applicable.')}

## Shared product requirements

{values.get('shared_requirements', '- Not applicable.')}

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
default. The selected compatible delivery profile is:

- [x] {values.get('target_family', 'Not recorded')}

Additional selected targets:

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

## Functional references

{values['references']}

## Art direction

{values['art_direction']}

## Open decisions

{values['open_decisions']}
"""


def _project_slug(project_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Project name must include letters or numbers.")
    return slug


def _set_env_values(path: Path, updates: dict[str, str]) -> None:
    """Replace selected project-owned .env values without disturbing secrets."""

    path = path.expanduser().resolve()
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = existing.splitlines()
    remaining = set(updates)
    rendered: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            remaining.remove(key)
        else:
            rendered.append(line)
    if remaining:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.append("# Art direction selected by `supervisor initial`.")
        rendered.extend(f"{key}={updates[key]}" for key in sorted(remaining))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def _record_initial_art_direction(project_slug: str, brief: str) -> str:
    """Configure the factory's art lane and return the brief wording to retain."""

    try:
        config = project_supervisor_checkout(Path.cwd()) / ".env"
    except RuntimeError:
        # Explicit-output uses outside a Supervisor project still creates a
        # useful brief; it simply has no local asset lane to configure.
        return (
            "- User direction: " + brief if brief else
            "- No custom direction supplied. Gemma 4 12B should create an original art direction when an asset lane is configured."
        )
    if brief:
        _set_env_values(config, {
            "ART_PRODUCT_SLUG": project_slug,
            "ART_DIRECTION_MODE": "user_provided",
            "ART_DIRECTION_MODEL": "gemma4:12b",
            "ART_DIRECTION_BRIEF": brief,
            "ART_STYLE_NAME": "user-directed original product art",
            "ART_STYLE_PROMPT": f"{brief}, original product asset, clear readable silhouette, no text, no logo",
        })
        return f"- User direction: {brief}\n- The project `.env` has been updated to use this direction."
    _set_env_values(config, {
        "ART_PRODUCT_SLUG": project_slug,
        "ART_DIRECTION_MODE": "gemma4_auto",
        "ART_DIRECTION_MODEL": "gemma4:12b",
        "ART_DIRECTION_BRIEF": "",
    })
    return "- No custom direction supplied. Gemma 4 12B will create an original art direction for asset work.\n- The project `.env` has been configured for Gemma 4 12B automatic art direction."


def create_initial_brief(path: Path, *, project_name: str, force: bool = False) -> Path:
    """Interactively collect a complete project brief and write INITIAL.md."""

    path = path.expanduser().resolve()
    if path.exists() and not force:
        raise ValueError(f"Initial brief already exists: {path}. Use --force to replace it.")
    project_slug = _project_slug(project_name)
    category = _choose_one("What type of product are you building", PRODUCT_CATEGORIES)
    target_family, additional_targets = _choose_compatible_targets(category)
    targets = [*DEFAULT_WEB_TARGETS, *additional_targets]
    game_characteristics = (
        [
            _choose_one_with_example("Choose the visual presentation", GAME_PRESENTATIONS, example="2D presentation."),
            _choose_one_with_example("Choose the player mode", GAME_PLAYER_MODES, example="Single-player game."),
            _choose_one_with_example("Choose the primary game genre", GAME_GENRES, example="Role-playing game (RPG)."),
        ]
        if category == "Game"
        else []
    )
    shared_requirements = "\n".join(
        f"- {requirement}" for requirement in _game_shared_requirement_options(game_characteristics)
    ) if category == "Game" else "\n".join(f"- {requirement}" for requirement in _application_shared_requirements(category))
    target_details = (
        {target: _game_target_requirements(target, game_characteristics) for target in targets}
        if category == "Game"
        else {target: "\n".join(f"- {requirement}" for requirement in _application_target_requirement_options(target)) for target in targets}
    )
    if category == "Game":
        player_experience = f"- {_choose_one_with_example('Choose the intended player experience', GAME_PLAYER_EXPERIENCE, example='Casual players.')}"
        audience_groups = f"- {_choose_one_with_example('Choose the intended audience group', GAME_AUDIENCE_GROUPS, example='Teen players.')}"
        users = "\n".join((player_experience, audience_groups))
        primary_outcome = _choose_one_with_example("Select the primary player outcome", GAME_PRIMARY_OUTCOMES, example="Progress through a story or campaign.")
        first_session = _choose_one_with_example("Select a successful first session", GAME_FIRST_SESSION_SUCCESSES, example="Finish a first battle and understand how saving works.")
        capabilities = "\n".join(f"- {capability}" for capability in GAME_FIRST_RELEASE_CAPABILITIES)
        deferred = _selected_bullets("Select deferred game capabilities", GAME_DEFERRED_CAPABILITIES)
        technology = "To be determined by the factory from the game format, selected platforms, and project brief."
        constraints = "To be determined by the factory from the game format, selected platforms, and project brief."
        non_goals = "No copied branding, assets, text, layouts, or distinctive interactions."
        parity = "Build every selected platform in tandem from one shared core, with feature parity by default and platform-specific input or UI adaptations only where necessary."
        sync = "Store player state remotely against the player account so it can be shared across selected devices wherever possible. Provide a local offline cache, safe synchronisation, and conflict recovery. Design the save-state service as a reusable platform capability rather than a game-specific silo."
        compliance = "Provide localisation support, privacy consent and player data controls, and age-rating or parental-control requirements. Apply platform-appropriate accessibility requirements (including WCAG guidance for web surfaces). Build the appropriate distributable package for every selected platform."
        support = "Support device classes and OS/browser versions that remain widely used for every selected platform. Make slow or offline network conditions usable wherever feasible without compromising the core game design or required online features."
    else:
        profile = APPLICATION_BRIEF_PROFILES[category]
        users = f"- {_choose_one_with_example('Choose the intended audience', profile['audiences'], example=profile['audiences'][0] + '.')}"
        primary_outcome = _choose_one_with_example("Choose the primary user outcome", profile["outcomes"], example=profile["outcomes"][0] + ".")
        first_session = _choose_one_with_example("Choose a successful first session", profile["first_sessions"], example=profile["first_sessions"][0] + ".")
        capabilities = "\n".join(f"- {capability}" for capability in profile["capabilities"])
        deferred = "\n".join(f"- {capability}" for capability in profile["deferred"])
        technology = "To be determined by the factory from the product category, selected platforms, and brief."
        constraints = "To be determined by the factory from the product category, selected platforms, and brief."
        non_goals = "No copied branding, assets, text, layouts, or distinctive interactions."
        parity = "Build every selected platform in tandem from one shared core, with feature parity by default and platform-specific input or UI adaptations only where necessary."
        sync = "Store user state remotely against the user account so it can be shared across selected devices wherever possible. Provide a local offline cache, safe synchronisation, and conflict recovery where the product supports offline work."
        compliance = "Provide localisation support, privacy consent and user data controls, and platform-appropriate accessibility requirements (including WCAG guidance for web surfaces). Build the appropriate distributable package for every selected platform."
        support = "Support device classes and OS/browser versions that remain widely used for every selected platform. Make slow or offline network conditions usable wherever feasible without compromising required online features."
    print("\nOptional art direction:")
    _print_example("Warm hand-painted fantasy, soft sunrise lighting, chunky readable silhouettes, parchment and moss palette.")
    print("Leave this blank and Gemma 4 12B will create an original art direction for asset work.")
    art_direction_input = " ".join(input("> ").split())
    values = {
        "project_name": project_name,
        "project_slug": project_slug,
        "product": _required("Describe what you are creating", example=PRODUCT_DESCRIPTION_EXAMPLES[category]),
        "category": category,
        "target_family": target_family,
        "game_characteristics": "\n".join(f"- [x] {item}" for item in game_characteristics) or "- Not specified.",
        "shared_requirements": shared_requirements,
        "users": users,
        "primary_outcome": primary_outcome,
        "first_session": first_session,
        "capabilities": capabilities,
        "deferred": deferred,
        "technology": technology,
        "constraints": constraints,
        "non_goals": non_goals,
        "parity": parity,
        "sync": sync,
        "compliance": compliance,
        "support": support,
        "references": _required(
            "Reference games, apps, or products that this should be functionally similar to",
            example=FUNCTIONAL_REFERENCE_EXAMPLES[category],
        ),
        "art_direction": _record_initial_art_direction(project_slug, art_direction_input),
        "open_decisions": "- Infer suitable technical services, including analytics, from the product brief and selected platforms.\n- Infer remaining product decisions from the functional references unless they conflict with an explicit requirement.\n- Record only genuine ambiguities that cannot be resolved safely from the available context.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_initial_brief(values, targets, target_details), encoding="utf-8")
    return path


def _collection_progress(runbooks_directory: Path, database_path: Path) -> tuple[int, int, str | None]:
    """Return accepted count, pending count, and the next runbook without creating state."""

    runbooks = sorted(
        (path for path in runbooks_directory.glob("*.md") if re.fullmatch(r"[A-Za-z]+\d+", path.stem)),
        key=lambda path: (load_task(path).sequence, path.name),
    ) if runbooks_directory.is_dir() else []
    if not runbooks:
        return 0, 0, None
    states: dict[str, str] = {}
    if database_path.is_file():
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        try:
            states = dict(connection.execute("SELECT task_id, status FROM task_state"))
        except sqlite3.OperationalError:
            states = {}
        finally:
            connection.close()
    accepted = sum(states.get(path.stem) == "accepted" for path in runbooks)
    next_runbook = next((path.stem for path in runbooks if states.get(path.stem) != "accepted"), None)
    return accepted, len(runbooks) - accepted, next_runbook


def project_statuses(projects_directory: Path, factory_runbooks_directory: Path) -> list[dict[str, str | int]]:
    """Summarise named runbook-factory workspaces using their durable state."""

    if not projects_directory.is_dir():
        return []
    statuses: list[dict[str, str | int]] = []
    for workspace in sorted(path for path in projects_directory.iterdir() if path.is_dir() and (path / "INITIAL.md").is_file()):
        collections = (
            ("R-series implementation", workspace / "runbooks", workspace / "runbooks" / ".supervisor" / "supervisor.sqlite3"),
            ("B-series authoring", workspace / "authoring-runbooks", workspace / "authoring-runbooks" / ".supervisor" / "supervisor.sqlite3"),
            ("F-series factory", factory_runbooks_directory, workspace / ".supervisor" / "factory.sqlite3"),
        )
        progress = [_collection_progress(*collection[1:]) for collection in collections]
        accepted = sum(item[0] for item in progress)
        pending = sum(item[1] for item in progress)
        active = next(((label, item[2]) for (label, *_paths), item in zip(collections, progress) if item[2]), None)
        phase = active[0] if active else ("Complete" if accepted else "Ready to start")
        statuses.append({
            "name": workspace.name,
            "brief": str(workspace / "INITIAL.md"),
            "phase": phase,
            "accepted": accepted,
            "pending": pending,
            "next": active[1] if active else "None",
        })
    return statuses


def print_projects(projects_directory: Path, factory_runbooks_directory: Path) -> None:
    statuses = project_statuses(projects_directory, factory_runbooks_directory)
    if not statuses:
        print(f"No named project workspaces found in {projects_directory}.")
        return
    for status in statuses:
        print(f"{status['name']}")
        print(f"  Phase: {status['phase']}")
        print(f"  Progress: {status['accepted']} accepted · {status['pending']} pending")
        print(f"  Next: {status['next']}")
        print(f"  Resume: supervisor-run --project {status['name']}")


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
        "Validation commands as a JSON array", test_commands_default, example='["npm test","npm run build"]'
    )
    values["SUPERVISOR_CODING_AGENTS"] = _prompt(
        "Coding agents in execution order", _value(existing, "SUPERVISOR_CODING_AGENTS"), example="qwen,openhands,codex"
    )
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
    if _git_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repository_root):
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
    if _git_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repository_root):
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
      Ask for a project name and write projects/<project-name>/INITIAL.md.
  supervisor projects
      List named project workspaces, progress, next task, and resume commands.
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
    initial_parser = commands.add_parser("initial", help="Interactively create a named project's brief consumed by the first factory runbook.")
    initial_parser.add_argument("--project-name", help="Project name; prompted for when omitted. The default brief path is projects/<project-name>/INITIAL.md.")
    initial_parser.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_DIRECTORY, help="Directory containing generated project workspaces (default: projects).")
    initial_parser.add_argument("--output", type=Path, help="Explicit brief path; normally omit this and let --project-name choose projects/<project-name>/INITIAL.md.")
    initial_parser.add_argument("--force", action="store_true", help="Replace an existing initial brief after collecting new answers.")
    projects_parser = commands.add_parser("projects", help="List named runbook-factory projects and their durable progress.")
    projects_parser.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_DIRECTORY, help="Directory containing named project workspaces (default: projects).")
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
            project_name = arguments.project_name or _required("Project name")
            project_slug = _project_slug(project_name)
            output = arguments.output or arguments.projects_dir / project_slug / "INITIAL.md"
            brief_path = create_initial_brief(output, project_name=project_name, force=arguments.force)
        except ValueError as error:
            parser.error(str(error))
        print(f"Wrote initial project brief: {brief_path}")
    if arguments.command == "projects":
        print_projects(arguments.projects_dir.expanduser().resolve(), Path.cwd() / "runbooks")
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
