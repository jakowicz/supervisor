"""Run local asset provenance and technical quality checks."""

from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, _repo_root) -> WorkerResult:
    return command_worker("asset QA reviewer", "ASSET_QA_COMMAND", task, NextStep.ASK_USER)
