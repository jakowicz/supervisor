"""Run the project-owned ComfyUI worker for an asset task."""

from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, _repo_root) -> WorkerResult:
    return command_worker("ComfyUI asset generator", "ASSET_GENERATOR_COMMAND", task, NextStep.ASK_USER)
