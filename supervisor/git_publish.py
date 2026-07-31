"""Publish only a fully accepted, isolated task worktree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .models import NextStep, Status, Task, WorkerResult


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)


def preflight(task: Task, root: Path) -> WorkerResult:
    """Require a clean baseline before agents can make an auto-committed change.

    Starting clean makes the final diff attributable to this task, so staging it
    wholesale cannot accidentally publish a user's unrelated local work.
    """

    if os.getenv("SUPERVISOR_AUTO_COMMIT") != "true":
        return WorkerResult(status=Status.PASS, summary="Auto-publish disabled; no clean-worktree preflight needed.", recommended_next_step=NextStep.COMPLETE)
    inside_repo = _run(["git", "rev-parse", "--is-inside-work-tree"], root)
    if inside_repo.returncode or inside_repo.stdout.strip() != "true":
        return WorkerResult(status=Status.NEEDS_USER_REVIEW, summary="Auto-publish requires a Git worktree.", test_result=inside_repo.stderr, recommended_next_step=NextStep.ASK_USER)
    status = _run(["git", "status", "--porcelain"], root)
    if status.returncode:
        return WorkerResult(status=Status.NEEDS_USER_REVIEW, summary="Could not inspect Git worktree before task.", test_result=status.stderr, recommended_next_step=NextStep.ASK_USER)
    if status.stdout.strip():
        return WorkerResult(
            status=Status.NEEDS_USER_REVIEW,
            summary="Auto-publish refused: start this task from a clean worktree so its commit is task-only.",
            test_result=status.stdout,
            recommended_next_step=NextStep.ASK_USER,
        )
    return WorkerResult(status=Status.PASS, summary=f"Clean Git baseline recorded for {task.task_id}.", recommended_next_step=NextStep.COMPLETE)


def publish(task: Task, root: Path) -> WorkerResult:
    if os.getenv("SUPERVISOR_AUTO_COMMIT") != "true":
        # Publishing is optional. Once every implementation and QA gate has
        # passed, a local/mock task is genuinely accepted even when the
        # operator has chosen not to let the supervisor write Git history.
        return WorkerResult(
            status=Status.PASS,
            summary="All validation passed; auto-commit is disabled, so no Git commit was created.",
            recommended_next_step=NextStep.COMPLETE,
        )
    status = _run(["git", "status", "--porcelain"], root)
    if status.returncode or not status.stdout.strip():
        return WorkerResult(status=Status.NEEDS_USER_REVIEW, summary="Publish requires a non-empty task-only Git diff.", test_result=status.stdout + status.stderr, recommended_next_step=NextStep.ASK_USER)
    check = _run(["git", "diff", "--check"], root)
    if check.returncode:
        return WorkerResult(status=Status.REPAIRABLE_FAILURE, summary="Git whitespace check failed; refusing to commit.", test_result=check.stdout + check.stderr, recommended_next_step=NextStep.RETRY_QWEN)
    add = _run(["git", "add", "-A"], root)
    commit = _run(["git", "commit", "-m", f"task({task.task_id.lower()}): {task.title}"], root)
    if add.returncode or commit.returncode:
        return WorkerResult(status=Status.ENVIRONMENT_FAILURE, summary="Git commit failed.", test_result=add.stderr + commit.stdout + commit.stderr, recommended_next_step=NextStep.ASK_USER)
    if os.getenv("SUPERVISOR_AUTO_PUSH") == "true":
        push = _run(["git", "push"], root)
        if push.returncode:
            return WorkerResult(status=Status.ENVIRONMENT_FAILURE, summary="Commit succeeded, but GitHub push failed.", test_result=push.stdout + push.stderr, recommended_next_step=NextStep.ASK_USER)
        return WorkerResult(status=Status.PASS, summary="Task committed and pushed after all QA gates passed.", recommended_next_step=NextStep.COMPLETE)
    return WorkerResult(status=Status.PASS, summary="Task committed locally after all QA gates passed.", recommended_next_step=NextStep.COMPLETE)
