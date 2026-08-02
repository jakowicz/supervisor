"""Run the product-owned ACE-Step local audio generator."""

from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, _repo_root) -> WorkerResult:
    return command_worker("ACE-Step local audio generator", "AUDIO_GENERATOR_COMMAND", task, NextStep.ASK_USER)
