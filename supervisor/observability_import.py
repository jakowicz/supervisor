"""Backfill existing local supervisor runs into Langfuse without changing SQLite."""

from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .models import Task, WorkerResult
from .observability import SupervisorTelemetry
from .reports_cli import open_database


def _task(payload: dict[str, Any]) -> Task:
    return Task.model_validate(payload["task"])


def import_run(telemetry: SupervisorTelemetry, payload: dict[str, Any], run_number: int) -> None:
    task = _task(payload)
    # The first local schema predates run IDs. Give those records a stable
    # synthetic ID so repeated imports group them consistently.
    legacy_key = f"{task.task_id}:{payload.get('created_at', '')}"
    run_id = payload.get("run_id") or f"legacy-{uuid.uuid5(uuid.NAMESPACE_URL, legacy_key)}"
    with telemetry.run(task, run_id, run_number) as run_span:
        for event in payload.get("events", []):
            result = WorkerResult.model_validate(event["result"])
            with telemetry.stage(task, run_id, event["stage"], event["agent"], event["model"], event["attempt"]) as stage_span:
                telemetry.complete_stage(stage_span, result, event["route"])
        telemetry.complete_run(run_span, payload["status"], payload["route"], len(payload.get("events", [])))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill local SQLite supervisor history into local Langfuse.")
    parser.add_argument("--database", type=Path, help="Override SUPERVISOR_DATABASE_PATH.")
    parser.add_argument("--task", help="Only import one task ID.")
    parser.add_argument("--limit", type=int, help="Import at most this many newest runs.")
    arguments = parser.parse_args()
    load_dotenv()
    package_root = Path(__file__).resolve().parents[1]
    database = arguments.database or Path(os.getenv("SUPERVISOR_DATABASE_PATH", ".state/supervisor.sqlite3"))
    if not database.is_absolute():
        database = package_root / database
    telemetry = SupervisorTelemetry.from_environment()
    if not telemetry.is_enabled:
        parser.error("Set SUPERVISOR_OBSERVABILITY_ENABLED=true before importing.")
    connection = open_database(database)
    try:
        query = "SELECT payload FROM task_runs"
        values: list[Any] = []
        if arguments.task:
            query += " WHERE task_id = ?"
            values.append(arguments.task)
        query += " ORDER BY rowid DESC"
        if arguments.limit:
            query += " LIMIT ?"
            values.append(arguments.limit)
        rows = connection.execute(query, values).fetchall()
        for ordinal, row in enumerate(reversed(rows), start=1):
            import json
            payload = json.loads(row["payload"])
            import_run(telemetry, payload, ordinal)
            print(f"Imported {payload['task']['task_id']} / {payload.get('run_id', 'legacy run')}")
    finally:
        connection.close()
        telemetry.flush()


if __name__ == "__main__":
    main()
