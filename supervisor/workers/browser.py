from ..models import NextStep, Task, WorkerResult
from .base import command_worker


def run(task: Task, dry_run: bool = False) -> WorkerResult:
    if task.browser_impact == "not_applicable":
        return WorkerResult(
            status="pass",
            summary="Browser QA not applicable to this runbook.",
            recommended_next_step="complete",
        )
    if dry_run:
        return WorkerResult(status="pass", summary="Dry-run browser worker passed.", recommended_next_step="complete")
    return command_worker("Browser QA", "BROWSER_QA_COMMAND", task, NextStep.USE_OPENHANDS)
