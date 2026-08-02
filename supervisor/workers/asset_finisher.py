"""Run deterministic local asset finishing configured by the project."""

from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, _repo_root) -> WorkerResult:
    return command_worker("asset finisher", "ASSET_FINISHER_COMMAND", task, NextStep.ASK_USER)
