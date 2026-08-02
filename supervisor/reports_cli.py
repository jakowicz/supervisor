"""Read-only terminal reports for supervisor SQLite run history."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .environment import load_project_environment, project_path


def database_path() -> Path:
    package_root = Path(__file__).resolve().parents[1]
    project_root = load_project_environment(package_root)
    return project_path(
        os.getenv("SUPERVISOR_DATABASE_PATH", ".state/supervisor.sqlite3"),
        project_root,
    )


def open_database(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"No supervisor database exists at {path}. Run a supervisor task first.")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def list_runs(connection: sqlite3.Connection, task_id: str | None, limit: int) -> None:
    query = "SELECT rowid AS ledger_rowid, task_id, route, status, created_at, payload FROM task_runs"
    parameters: list[Any] = []
    if task_id:
        query += " WHERE task_id = ?"
        parameters.append(task_id)
    query += " ORDER BY rowid DESC LIMIT ?"
    parameters.append(limit)
    rows = connection.execute(query, parameters).fetchall()
    if not rows:
        print("No matching supervisor runs.")
        return
    print(f"{'RUN ID':36}  {'TASK':8}  {'MODE':10}  {'STATUS':22}  {'CREATED'}")
    for row in rows:
        payload = json.loads(row["payload"])
        mode = "dry-run" if any("Dry-run" in event.get("summary", "") for event in payload.get("events", [])) else "real"
        run_id = payload.get("run_id", f"legacy-row-{row['ledger_rowid']}")
        print(f"{run_id:36}  {row['task_id']:8}  {mode:10}  {row['status']:22}  {row['created_at']}")


def active_live_runs(path: Path) -> list[tuple[str, str, str]]:
    """Find unfinished supervisor logs, which intentionally precede SQLite."""

    live_directory = path.parent / "live"
    if not live_directory.exists():
        return []
    active: list[tuple[str, str, str]] = []
    for log in live_directory.glob("*.log"):
        text = log.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"RUN\s+(?:([\w-]+)\s+·\s+)?([0-9a-f-]{36})(?:\s+·\s+(D\d+))?", text)
        if match and "FINAL " not in text:
            task_id = match.group(3) or (match.group(1) or "").split("-")[0].upper()
            if re.fullmatch(r"D\d+", task_id):
                active.append((task_id, match.group(2), log.name))
    return sorted(active)


def load_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = connection.execute("SELECT payload FROM task_runs ORDER BY rowid DESC").fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("run_id") == run_id:
            return payload
    raise LookupError(f"Run {run_id} was not found.")


def show_summary(run: dict[str, Any]) -> None:
    print(f"{run['task']['task_id']} — {run['task']['title']}")
    print(f"Run: {run['run_id']}")
    print(f"Outcome: {run['status']} ({run['route']})\n")
    for event in run.get("events", []):
        print(f"{event['agent']} · attempt {event['attempt']} · {event['status']} → {event['route']}")
        print(f"  {event['summary']}")


def show_events(connection: sqlite3.Connection, run_id: str, raw: bool = False) -> None:
    rows = connection.execute(
        "SELECT timestamp, stage, agent, model, attempt, status, route, summary, payload "
        "FROM run_events WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    if not rows:
        print("No detailed events for this run.")
        return
    for row in rows:
        print(f"\n[{row['timestamp']}] {row['stage']} / {row['agent']} / {row['model']}")
        print(f"Attempt {row['attempt']} · {row['status']} → {row['route']}")
        print(row['summary'])
        result = json.loads(row['payload'])['result']
        if result['changed_files']:
            print("Changed: " + ", ".join(result['changed_files']))
        if result['test_result']:
            print("Tests: " + result['test_result'])
        evidence = result['evidence']
        if evidence['screenshots']:
            print("Screenshots: " + ", ".join(evidence['screenshots']))
        if raw:
            print_raw_output(row['stage'], evidence)


def show_task_state(connection: sqlite3.Connection, task_id: str, checkpoint_limit: int) -> None:
    """Print the compact recovery state plus the latest durable checkpoints."""

    state = connection.execute("SELECT * FROM task_state WHERE task_id = ?", (task_id,)).fetchone()
    if state is None:
        print(f"No durable state exists for {task_id}.")
        return
    print(f"{task_id} durable task state")
    for label, key in (
        ("Status", "status"),
        ("Active run", "active_run_id"),
        ("Active PID", "active_pid"),
        ("Qwen session", "agent_session_id"),
        ("Next action", "next_action"),
        ("Last update", "updated_at"),
    ):
        print(f"{label}: {state[key] or '—'}")
    print(f"Summary: {state['continuation_summary'] or '—'}")
    try:
        changed_files = json.loads(state["changed_files_json"])
    except json.JSONDecodeError:
        changed_files = []
    print(f"Changed files snapshot: {len(changed_files)}")

    rows = connection.execute(
        "SELECT timestamp, stage, agent, kind, payload FROM run_checkpoints "
        "WHERE task_id = ? ORDER BY id DESC LIMIT ?",
        (task_id, checkpoint_limit),
    ).fetchall()
    if not rows:
        return
    print("\nRecent checkpoints:")
    for row in rows:
        payload = json.loads(row["payload"])
        summary = str(payload.get("summary", "—")).replace("\n", " ")
        next_action = payload.get("next_action", "—")
        print(f"{row['timestamp']}  {row['stage']:12}  {row['kind']:16}  {next_action}")
        print(f"  {summary[:220]}")


def print_raw_output(stage: str, evidence: dict[str, Any]) -> None:
    for label, key in (
        ("RAW AGENT OUTPUT", "agent_log"),
        ("ADAPTER OUTPUT", "adapter_log"),
        ("TEST OUTPUT", "test_log"),
        ("BROWSER OUTPUT", "browser_log"),
    ):
        output = evidence.get(key, "")
        if output:
            print(f"\n===== {stage}: {label} =====\n{output}")


def browse(connection: sqlite3.Connection, path: Path) -> None:
    """Small interactive TTY navigator for task → run → stage evidence."""

    active = active_live_runs(path)
    if active:
        print("ACTIVE RUNS — still running; not yet in SQLite:")
        for index, (task_id, run_id, filename) in enumerate(active, start=1):
            print(f"  a{index}. {task_id} · {run_id} · {filename}")
        print()
    task_rows = connection.execute(
        "SELECT task_id, COUNT(*) AS run_count FROM task_runs GROUP BY task_id ORDER BY task_id"
    ).fetchall()
    if not task_rows:
        print("No supervisor runs.")
        return
    print("Tasks:")
    for index, row in enumerate(task_rows, start=1):
        print(f"  {index}. {row['task_id']} ({row['run_count']} runs)")
    selected = input("Select task, active run (a1), or q: ").strip().lower()
    if selected == "q":
        return
    if selected.startswith("a") and selected[1:].isdigit():
        try:
            task_id, run_id, filename = active[int(selected[1:]) - 1]
        except IndexError:
            print("Invalid active-run selection.")
            return
        live_log = path.parent / "live" / filename
        print(f"\nACTIVE {task_id} — {run_id}\nLive log: {live_log}\n")
        print(live_log.read_text(encoding="utf-8", errors="replace"))
        return
    try:
        task_id = task_rows[int(selected) - 1]["task_id"]
    except (ValueError, IndexError):
        print("Invalid task selection.")
        return
    rows = connection.execute(
        "SELECT payload FROM task_runs WHERE task_id = ? ORDER BY rowid DESC", (task_id,)
    ).fetchall()
    # Early experimental records did not include a run_id in the payload and
    # cannot be opened by the run-detail interface. Keep them listable in the
    # non-interactive report, but omit them from this selector.
    runs = [payload for row in rows if (payload := json.loads(row["payload"])).get("run_id")]
    print(f"\n{task_id} runs:")
    for index, run in enumerate(runs, start=1):
        print(f"  {index}. {run['run_id']} · {run['status']} · {run['created_at']}")
    selected = input("Select run (or q): ").strip().lower()
    if selected == "q":
        return
    try:
        run = runs[int(selected) - 1]
    except (ValueError, IndexError):
        print("Invalid run selection.")
        return
    show_summary(run)
    events = run.get("events", [])
    if not events:
        return
    print("\nStages:")
    for index, event in enumerate(events, start=1):
        print(f"  {index}. {event['stage']} · {event['agent']} · {event['status']}")
    selected = input("Select stage for full output (or a for all, q to quit): ").strip().lower()
    if selected == "a":
        for event in events:
            print_raw_output(event["stage"], event["result"]["evidence"])
        return
    if selected == "q":
        return
    try:
        event = events[int(selected) - 1]
    except (ValueError, IndexError):
        print("Invalid stage selection.")
        return
    print_raw_output(event["stage"], event["result"]["evidence"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect read-only Supervisor execution reports, evidence, and recovery state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  supervisor-reports list
      Show recent runs and any active local run logs.
  supervisor-reports list --task R0007
      Limit the list to one task ID.
  supervisor-reports task-state R0007
      Inspect the durable state and checkpoints used for recovery.
  supervisor-reports show <run-id>
      Show the concise accepted/failed progression for one run.
  supervisor-reports events <run-id> --raw
      Show all detailed stage evidence, including preserved worker output.
  supervisor-reports export <run-id> --output run.json
      Export the complete stored run record without modifying it.

Reports are read-only. Run supervisor-run to execute or retry work.""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List recent task runs.")
    list_parser.add_argument("--task", help="Filter by task ID, for example D006.")
    list_parser.add_argument("--limit", type=int, default=20)
    show_parser = subparsers.add_parser("show", help="Show the concise progression for a run.")
    show_parser.add_argument("run_id")
    events_parser = subparsers.add_parser("events", help="Show detailed agent/test/browser events.")
    events_parser.add_argument("run_id")
    events_parser.add_argument("--raw", action="store_true", help="Include preserved raw stdout/stderr for every stage.")
    state_parser = subparsers.add_parser("task-state", help="Show durable recovery state and recent checkpoints for a task.")
    state_parser.add_argument("task_id", help="Task ID, for example D006.")
    state_parser.add_argument("--limit", type=int, default=12, help="Number of recent checkpoints to show.")
    subparsers.add_parser("browse", help="Interactively select task, run, then stage output.")
    export_parser = subparsers.add_parser("export", help="Export a complete run JSON document.")
    export_parser.add_argument("run_id")
    export_parser.add_argument("--output", help="Write JSON to this file instead of stdout.")
    if len(sys.argv) == 1:
        parser.print_help()
        return
    arguments = parser.parse_args()
    try:
        connection = open_database(database_path())
        try:
            if arguments.command == "list":
                active = [run for run in active_live_runs(database_path()) if not arguments.task or run[0] == arguments.task]
                if active:
                    print("ACTIVE RUNS — not yet recorded in SQLite")
                    for task_id, run_id, filename in active:
                        print(f"{task_id:8}  {run_id:36}  {filename}")
                    print()
                list_runs(connection, arguments.task, arguments.limit)
                return
            if arguments.command == "browse":
                browse(connection, database_path())
                return
            if arguments.command == "task-state":
                show_task_state(connection, arguments.task_id, arguments.limit)
                return
            run = load_run(connection, arguments.run_id)
            if arguments.command == "show":
                show_summary(run)
            elif arguments.command == "events":
                show_events(connection, arguments.run_id, arguments.raw)
            else:
                document = json.dumps(run, indent=2)
                if arguments.output:
                    Path(arguments.output).write_text(document + "\n", encoding="utf-8")
                else:
                    print(document)
        finally:
            connection.close()
    except (FileNotFoundError, LookupError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
