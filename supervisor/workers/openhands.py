from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task) -> WorkerResult:
    return command_worker("OpenHands", "OPENHANDS_COMMAND", task, NextStep.USE_CODEX)
