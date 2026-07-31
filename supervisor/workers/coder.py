from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task) -> WorkerResult:
    return command_worker("Qwen3 Coder", "QWEN_CODER_COMMAND", task, NextStep.USE_OPENHANDS)
