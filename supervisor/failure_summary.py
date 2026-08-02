"""Concise, local-only operator summaries for unsuccessful supervisor runs."""

from __future__ import annotations

import json
import os
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

from .models import TaskRun


def _failure_evidence(run: TaskRun) -> str:
    for event in reversed(run.events):
        if event.status.value == "pass":
            continue
        result = event.result
        evidence = "\n".join(
            part for part in (
                result.test_result,
                result.evidence.test_log,
                result.evidence.browser_log,
                result.evidence.agent_log,
                result.evidence.adapter_log,
            ) if part
        )
        return f"Stage: {event.stage}\nSummary: {event.summary}\n\n{evidence[-24000:]}"
    return "No non-passing stage evidence was recorded."


def _deterministic_summary(evidence: str) -> str:
    """Useful fallback when a local model is unavailable or occupied."""

    lines: list[str] = []
    for line in evidence.splitlines():
        compact = line.strip()
        if not compact or not re.search(r"failed|error|exception|\[E\]|golden", compact, re.IGNORECASE):
            continue
        if compact not in lines:
            lines.append(compact)
    bullets = lines[:8] or ["The final stage returned a non-zero result; inspect the linked stage log."]
    return "Local failure digest (deterministic fallback):\n" + "\n".join(f"- {line}" for line in bullets)


def summarize_failure(run: TaskRun) -> str:
    """Ask local Ollama for concise JSON, with an always-available fallback."""

    evidence = _failure_evidence(run)
    if os.getenv("SUPERVISOR_FAILURE_SUMMARY_ENABLED", "true").lower() != "true":
        return _deterministic_summary(evidence)
    payload = {
        "model": os.getenv("SUPERVISOR_FAILURE_SUMMARY_MODEL", "gemma4:12b"),
        "stream": False,
        "format": "json",
        "keep_alive": os.getenv("SUPERVISOR_FAILURE_SUMMARY_KEEP_ALIVE", "0"),
        "options": {"temperature": 0, "num_predict": 450},
        "prompt": (
            "Summarise this local supervisor failure for a developer. Return JSON only with "
            "{\"failures\":[string],\"next_action\":string}. State concrete failing tests/errors, "
            "not generic advice. Maximum six short failure strings.\n\n" + evidence
        ),
    }
    try:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        request = Request(f"{base_url}/api/generate", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=int(os.getenv("SUPERVISOR_FAILURE_SUMMARY_TIMEOUT_SECONDS", "180"))) as response:
            model_response = json.loads(response.read().decode("utf-8"))
        summary = json.loads(model_response["response"])
        failures = summary.get("failures")
        next_action = summary.get("next_action")
        if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures) or not isinstance(next_action, str):
            raise ValueError("model response did not match the required failure-summary shape")
        return "Local Gemma failure digest:\n" + "\n".join(f"- {item}" for item in failures[:6]) + f"\nNext action: {next_action}"
    except (OSError, URLError, ValueError, KeyError, json.JSONDecodeError) as error:
        return _deterministic_summary(evidence) + f"\n(Gemma summary unavailable: {error})"
