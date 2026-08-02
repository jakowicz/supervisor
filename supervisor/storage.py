"""Durable, local-only records for task runs and their evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TaskRun, model_to_dict


class RunStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                route TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_state (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_run_id TEXT,
                active_pid INTEGER,
                agent_session_id TEXT,
                next_action TEXT NOT NULL DEFAULT '',
                continuation_summary TEXT NOT NULL DEFAULT '',
                changed_files_json TEXT NOT NULL DEFAULT '[]',
                diff_fingerprint TEXT NOT NULL DEFAULT '',
                accepted_commit TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                agent TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                agent TEXT NOT NULL,
                model TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                route TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def state_for(self, task_id: str) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM task_state WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        columns = [column[1] for column in self._connection.execute("PRAGMA table_info(task_state)")]
        return dict(zip(columns, row))

    def claim_task(self, task_id: str, run_id: str, pid: int) -> dict[str, Any] | None:
        """Claim a task and return its previous state for automatic continuation."""

        previous = self.state_for(task_id)
        if previous and previous.get("active_run_id") and previous["active_run_id"] != run_id:
            active_pid = previous.get("active_pid")
            if active_pid and self._pid_is_alive(int(active_pid)):
                raise RuntimeError(
                    f"{task_id} is already being supervised by run {previous['active_run_id']} (PID {active_pid})."
                )
            self._connection.execute(
                "UPDATE task_state SET status = ?, active_run_id = NULL, active_pid = NULL, updated_at = ? WHERE task_id = ?",
                ("interrupted", self._now(), task_id),
            )
        self._connection.execute(
            """INSERT INTO task_state (task_id, status, active_run_id, active_pid, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                 status = excluded.status, active_run_id = excluded.active_run_id,
                 active_pid = excluded.active_pid, updated_at = excluded.updated_at""",
            (task_id, "implementing", run_id, pid, self._now()),
        )
        self._connection.commit()
        return previous

    def checkpoint(
        self, run_id: str, task_id: str, stage: str, agent: str, kind: str, payload: dict[str, Any]
    ) -> None:
        """Commit one progress fact immediately; safe to call while an agent runs."""

        timestamp = self._now()
        self._connection.execute(
            "INSERT INTO run_checkpoints (run_id, task_id, timestamp, stage, agent, kind, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, task_id, timestamp, stage, agent, kind, json.dumps(payload, default=str)),
        )
        session_id = payload.get("session_id")
        next_action = payload.get("next_action")
        summary = payload.get("summary")
        changed_files = payload.get("changed_files")
        fingerprint = payload.get("diff_fingerprint")
        updates: list[str] = ["updated_at = ?"]
        values: list[Any] = [timestamp]
        if session_id:
            updates.append("agent_session_id = ?")
            values.append(session_id)
        if next_action is not None:
            updates.append("next_action = ?")
            values.append(str(next_action))
        if summary is not None:
            updates.append("continuation_summary = ?")
            values.append(str(summary))
        if changed_files is not None:
            updates.append("changed_files_json = ?")
            values.append(json.dumps(changed_files))
        if fingerprint is not None:
            updates.append("diff_fingerprint = ?")
            values.append(str(fingerprint))
        values.append(task_id)
        self._connection.execute(f"UPDATE task_state SET {', '.join(updates)} WHERE task_id = ?", values)
        self._connection.commit()

    def finish_task(self, run: TaskRun, accepted_commit: str | None = None) -> None:
        # Preserve an explicit terminal review state.  Reclassifying every
        # non-pass as ``validating`` makes a later collection invocation enter
        # validation with no candidate result after an environment failure.
        status = "accepted" if run.status.value == "pass" else run.status.value
        self._connection.execute(
            """UPDATE task_state SET status = ?, active_run_id = NULL, active_pid = NULL,
               next_action = CASE WHEN ? = 'accepted' THEN 'already_complete'
                                  WHEN next_action = '' THEN 'prepare'
                                  ELSE next_action END,
               accepted_commit = COALESCE(?, accepted_commit), updated_at = ? WHERE task_id = ?""",
            (status, status, accepted_commit, self._now(), run.task.task_id),
        )
        self._connection.commit()

    def abandon_task(self, task_id: str, run_id: str, reason: str) -> None:
        self._connection.execute(
            """UPDATE task_state SET status = 'interrupted', active_run_id = NULL, active_pid = NULL,
               next_action = ?, continuation_summary = ?, updated_at = ?
               WHERE task_id = ? AND active_run_id = ?""",
            ("resume_from_checkpoint", reason, self._now(), task_id, run_id),
        )
        self._connection.commit()

    def reopen_task(self, task_id: str, reason: str, next_action: str = "codex") -> None:
        """Return an incorrectly accepted task to its repair stage.

        This is deliberately explicit rather than silently treating bad output
        as accepted: the prior run evidence remains intact for diagnosis.
        """

        self._connection.execute(
            """UPDATE task_state SET status = 'interrupted', active_run_id = NULL, active_pid = NULL,
               next_action = ?, continuation_summary = ?, accepted_commit = NULL, updated_at = ?
               WHERE task_id = ?""",
            (next_action, reason, self._now(), task_id),
        )
        self._connection.commit()

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def save(self, run: TaskRun) -> None:
        payload = model_to_dict(run)
        self._connection.execute(
            "INSERT INTO task_runs (task_id, route, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (run.task.task_id, run.route, run.status.value, json.dumps(payload), run.created_at.isoformat()),
        )
        for event in run.events:
            event_payload = model_to_dict(event)
            self._connection.execute(
                """INSERT INTO run_events
                (run_id, task_id, timestamp, stage, agent, model, attempt, status, route, summary, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run.run_id, run.task.task_id, event.timestamp.isoformat(), event.stage, event.agent,
                 event.model, event.attempt, event.status.value, event.route, event.summary,
                 json.dumps(event_payload)),
            )
        self._connection.commit()
        self._write_operator_summary(run)

    def next_task_run_number(self, task_id: str) -> int:
        """Return the next human-friendly ordinal for one task's live log."""

        completed = self._connection.execute("SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()
        active = self._connection.execute(
            "SELECT COUNT(DISTINCT run_id) FROM run_checkpoints WHERE task_id = ? AND run_id NOT IN (SELECT json_extract(payload, '$.run_id') FROM task_runs)",
            (task_id,),
        ).fetchone()
        return int(completed[0]) + int(active[0]) + 1

    def _write_operator_summary(self, run: TaskRun) -> None:
        """Write a skim-friendly companion to the full SQLite event ledger."""

        report_directory = Path(self._connection.execute("PRAGMA database_list").fetchone()[2]).parent / "reports"
        report_directory.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# {run.task.task_id} — {run.task.title}",
            "",
            f"Run: `{run.run_id}`",
            f"Outcome: **{run.status.value}** (`{run.route}`)",
            "",
            "## Progress summary",
            "",
        ]
        for event in run.events:
            lines.append(
                f"- {event.agent} · attempt {event.attempt} · {event.status.value} → "
                f"{event.route}: {event.summary}"
            )
        failure_digest = next((note for note in reversed(run.notes) if note.startswith("Failure digest:")), "")
        if failure_digest:
            lines.extend(["", "## Failure digest", "", failure_digest, ""])
        lines.extend([
            "",
            "The detailed event payloads, test/browser logs, and evidence paths are in `.state/supervisor.sqlite3`.",
            "",
        ])
        (report_directory / f"{run.run_id}.md").write_text("\n".join(lines), encoding="utf-8")

    def close(self) -> None:
        self._connection.close()
