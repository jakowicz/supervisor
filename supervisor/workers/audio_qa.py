"""Run deterministic product-owned audio QA."""

from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, _repo_root) -> WorkerResult:
    return command_worker("local audio QA", "AUDIO_QA_COMMAND", task, NextStep.ASK_USER)
