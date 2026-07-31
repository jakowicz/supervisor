from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task) -> WorkerResult:
    return command_worker("Codex worker", "CODEX_COMMAND", task, NextStep.ASK_USER)
