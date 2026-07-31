"""Independent Flutter build/test worker."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from ..models import Evidence, NextStep, Status, Task, WorkerResult


def run(task: Task, repo_root: Path, dry_run: bool = False) -> WorkerResult:
    if dry_run:
        return WorkerResult(status=Status.PASS, summary="Dry-run test worker passed.", recommended_next_step=NextStep.COMPLETE)
    logs: list[str] = []
    configured_command = os.getenv("SUPERVISOR_TEST_COMMAND")
    commands = [shlex.split(configured_command)] if configured_command else [
        ["flutter", "analyze"], ["flutter", "test"], ["flutter", "build", "web", "--release"],
    ]
    if configured_command and not commands[0]:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary="SUPERVISOR_TEST_COMMAND is empty or invalid.",
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
        summary=(f"Configured project checks passed for {task.task_id}." if configured_command else f"Flutter checks passed for {task.task_id}."),
        test_result=(f"{' '.join(commands[0])} passed." if configured_command else "flutter analyze, flutter test, and flutter build web --release passed."),
        evidence=Evidence(test_log="\n".join(logs)),
        recommended_next_step=NextStep.COMPLETE,
    )
