"""Independent project-configured validation worker."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from ..models import Evidence, NextStep, Status, Task, WorkerResult


def _configured_commands() -> list[list[str]] | None:
    """Load the project validation contract without imposing a framework."""

    commands_json = os.getenv("SUPERVISOR_TEST_COMMANDS")
    if commands_json is not None:
        try:
            command_strings = json.loads(commands_json)
        except json.JSONDecodeError as error:
            raise ValueError("SUPERVISOR_TEST_COMMANDS must be a JSON array of command strings.") from error
        if not isinstance(command_strings, list) or not command_strings or not all(isinstance(command, str) and command.strip() for command in command_strings):
            raise ValueError("SUPERVISOR_TEST_COMMANDS must be a non-empty JSON array of command strings.")
        return [shlex.split(command) for command in command_strings]
    # Legacy project configuration remains supported, but new projects should
    # use the ordered plural setting so each validation command has evidence.
    configured_command = os.getenv("SUPERVISOR_TEST_COMMAND")
    return [shlex.split(configured_command)] if configured_command else None


def run(task: Task, repo_root: Path, dry_run: bool = False) -> WorkerResult:
    if dry_run:
        return WorkerResult(status=Status.PASS, summary="Dry-run test worker passed.", recommended_next_step=NextStep.COMPLETE)
    logs: list[str] = []
    try:
        commands = _configured_commands()
    except ValueError as error:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary=str(error),
            recommended_next_step=NextStep.ASK_USER,
        )
    if not commands:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary="No project validation contract is configured. Set SUPERVISOR_TEST_COMMANDS via `supervisor configure`.",
            recommended_next_step=NextStep.ASK_USER,
        )
    for command in commands:
        completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=False, timeout=1800)
        output = "$ " + " ".join(command) + "\n" + completed.stdout + completed.stderr
        logs.append(output)
        if completed.returncode != 0:
            return WorkerResult(
                status=Status.REPAIRABLE_FAILURE,
                summary=f"Independent test worker failed: {' '.join(command)}",
                test_result=output,
                evidence=Evidence(test_log="\n".join(logs)),
                recommended_next_step=NextStep.RETRY_QWEN,
            )
    return WorkerResult(
        status=Status.PASS,
        summary=f"Configured project checks passed for {task.task_id}.",
        test_result="; ".join(f"{' '.join(command)} passed" for command in commands) + ".",
        evidence=Evidence(test_log="\n".join(logs)),
        recommended_next_step=NextStep.COMPLETE,
    )
