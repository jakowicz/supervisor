"""Recover completed coding results from live evidence after an interruption."""

from __future__ import annotations

from pathlib import Path

from .models import WorkerResult
from .result_parser import parse_worker_result


def qwen_logs(live_directory: Path, task_id: str) -> list[Path]:
    """Return all task-specific Qwen logs, newest first."""

    pattern = f"*{task_id.lower()}*qwen*.log"
    return sorted(live_directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)


def latest_qwen_result(live_directory: Path, task_id: str) -> tuple[WorkerResult, Path] | None:
    """Return the newest parseable Qwen result, skipping incomplete retry logs."""

    for path in qwen_logs(live_directory, task_id):
        result = parse_worker_result(path.read_text(encoding="utf-8", errors="replace"))
        if result is not None:
            return result, path
    return None
