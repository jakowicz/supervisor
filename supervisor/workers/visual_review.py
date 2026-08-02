from ..models import NextStep, Status, Task, WorkerResult
from .base import command_worker


def run(task: Task, dry_run: bool = False) -> WorkerResult:
    if task.browser_impact == "not_applicable":
        return WorkerResult(
            status=Status.PASS,
            summary="Visual review not applicable to this runbook.",
            recommended_next_step=NextStep.COMPLETE,
        )
    if dry_run:
        return WorkerResult(
            status=Status.NEEDS_USER_REVIEW,
            summary="Dry run has no independent visual reviewer; user review is required.",
            recommended_next_step=NextStep.ASK_USER,
        )
    return command_worker("Visual reviewer", "VISUAL_REVIEW_COMMAND", task, NextStep.ASK_USER)
