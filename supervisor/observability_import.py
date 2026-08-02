"""Backfill existing local supervisor runs into Langfuse without changing SQLite."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

from .environment import load_project_environment, project_path
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
    parser = argparse.ArgumentParser(
        description="Backfill local SQLite Supervisor history into configured Langfuse observability.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  supervisor-observability-import --all
      Import all stored local runs into the configured Langfuse project.
  supervisor-observability-import --task R0007
      Import only one task's history.
  supervisor-observability-import --limit 20
      Import at most the twenty newest stored runs.
  supervisor-observability-import --database path/to/supervisor.sqlite3
      Read a specific SQLite database instead of SUPERVISOR_DATABASE_PATH.

Set SUPERVISOR_OBSERVABILITY_ENABLED=true and the Langfuse configuration first.
This command reads SQLite history; it does not execute or alter task work.""",
    )
    parser.add_argument("--all", action="store_true", help="Import all runs from the selected database. Running with no arguments shows this help.")
    parser.add_argument("--database", type=Path, help="Override SUPERVISOR_DATABASE_PATH.")
    parser.add_argument("--task", help="Only import one task ID.")
    parser.add_argument("--limit", type=int, help="Import at most this many newest runs.")
    if len(sys.argv) == 1:
        parser.print_help()
        return
    arguments = parser.parse_args()
    if not arguments.all and not arguments.task:
        parser.error("Pass --all to import all runs, or --task to import one task. Run without arguments for usage.")
    package_root = Path(__file__).resolve().parents[1]
    project_root = load_project_environment(package_root)
    database = arguments.database or project_path(
        os.getenv("SUPERVISOR_DATABASE_PATH", ".state/supervisor.sqlite3"),
        project_root,
    )
    if not database.is_absolute():
        database = project_path(database, project_root)
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
