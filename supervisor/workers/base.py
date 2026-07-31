"""Safe command-based worker adapter utilities."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path

from ..models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict


def worker_timeout_seconds() -> int:
    return int(os.getenv("SUPERVISOR_WORKER_TIMEOUT_SECONDS", "1800"))


def _descendant_pids(root_pid: int) -> list[int]:
    """Return process descendants, deepest-first, including sandbox children."""

    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=False
        ).stdout.splitlines()
    except OSError:
        return []
    children: dict[int, list[int]] = {}
    for line in listing:
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            continue
        pid, parent = map(int, fields)
        children.setdefault(parent, []).append(pid)

    descendants: list[int] = []

    def visit(parent: int) -> None:
        for child in children.get(parent, []):
            visit(child)
            descendants.append(child)

    visit(root_pid)
    return descendants


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    """End a worker plus sandbox descendants that create their own groups."""

    pids = _descendant_pids(process.pid)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def unavailable(worker_name: str, next_step: NextStep) -> WorkerResult:
    return WorkerResult(
        status=Status.ENVIRONMENT_FAILURE,
        summary=f"{worker_name} is not configured. Add its command to .env before running a real task.",
        recommended_next_step=next_step,
    )


def command_worker(worker_name: str, environment_key: str, task: Task, next_step: NextStep) -> WorkerResult:
    """Run an explicitly configured worker and parse its one JSON response."""

    command_template = os.getenv(environment_key)
    if not command_template:
        return unavailable(worker_name, next_step)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(model_to_dict(task), handle)
        task_path = handle.name

    try:
        command = shlex.split(command_template.format(task_file=task_path))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=worker_timeout_seconds())
        except subprocess.TimeoutExpired as error:
            _stop_process_group(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command, worker_timeout_seconds(), output=stdout, stderr=stderr
            ) from error
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary=f"{worker_name} could not start: {error}",
            evidence=Evidence(test_log=str(error)),
            recommended_next_step=next_step,
        )
    finally:
        Path(task_path).unlink(missing_ok=True)

    if process.returncode != 0:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary=f"{worker_name} exited with code {process.returncode}.",
            evidence=Evidence(test_log=stdout + stderr),
            recommended_next_step=next_step,
        )
    try:
        result = WorkerResult.model_validate_json(stdout)
        result.evidence.adapter_log = stdout + stderr
        return result
    except ValueError as error:
        return WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary=f"{worker_name} did not return valid WorkerResult JSON: {error}",
            evidence=Evidence(test_log=stdout + stderr),
            recommended_next_step=next_step,
        )
