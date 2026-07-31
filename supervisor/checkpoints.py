"""Extract durable continuation facts from a live coding-agent stream."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def diff_snapshot(repo_root: Path) -> dict[str, Any]:
    """Return a cheap deterministic worktree fingerprint without changing Git state."""

    status = subprocess.run(
        # `repo_root` may be a small mock project nested inside a larger Git
        # checkout. The pathspec prevents unrelated parent-worktree changes
        # from bloating continuation prompts and fingerprints.
        ["git", "status", "--short", "--", "."],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    files = [line[3:] for line in status.splitlines() if len(line) > 3]
    diff = subprocess.run(
        ["git", "diff", "--binary", "--", "."],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    return {"changed_files": files, "diff_fingerprint": hashlib.sha256(diff.encode()).hexdigest()}


def stream_checkpoint(log_path: Path, offset: int = 0) -> tuple[dict[str, Any], int]:
    """Read appended JSONL events and return the latest useful continuation fact."""

    if not log_path.exists():
        return {}, offset
    with log_path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
        next_offset = handle.tell()
    latest: dict[str, Any] = {}
    for raw_line in chunk.decode("utf-8", errors="replace").splitlines():
        raw_line = raw_line.removeprefix("[stdout] ")
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            latest["session_id"] = event.get("session_id")
            latest["summary"] = f"Qwen session started with model {event.get('model', 'unknown')}."
            latest["next_action"] = "continue_current_agent_session"
        elif event.get("type") == "assistant":
            message = event.get("message", {})
            parts = message.get("content", []) if isinstance(message, dict) else []
            tools = [part.get("name") for part in parts if isinstance(part, dict) and part.get("type") == "tool_use"]
            if tools:
                latest["summary"] = f"Agent last requested: {', '.join(tools)}."
                latest["next_action"] = "continue_from_last_agent_tool"
        elif event.get("type") == "result":
            latest["summary"] = str(event.get("result", "Agent returned a final result."))[-2000:]
            latest["next_action"] = "validate_agent_result"
    return latest, next_offset


def stream_delta(log_path: Path, offset: int = 0) -> tuple[str, int]:
    """Read newly appended worker output for a real-time telemetry event."""

    if not log_path.exists():
        return "", offset
    with log_path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
        next_offset = handle.tell()
    return chunk.decode("utf-8", errors="replace"), next_offset


def continuation_brief(state: dict[str, Any] | None) -> str:
    """A small deterministic handoff inserted into a resumed agent prompt."""

    if not state:
        return ""
    parts = [
        "Continue the existing task; do not reimplement completed work.",
        f"Previous lifecycle: {state.get('status', 'unknown')}.",
    ]
    if state.get("changed_files_json"):
        try:
            files = json.loads(state["changed_files_json"])
        except (TypeError, json.JSONDecodeError):
            files = []
        if files:
            parts.append("Existing changed files: " + ", ".join(files[:30]) + ".")
    if state.get("continuation_summary"):
        parts.append("Last checkpoint: " + str(state["continuation_summary"]))
    if state.get("next_action"):
        parts.append("Required next action: " + str(state["next_action"]) + ".")
    return "\n".join(parts)
