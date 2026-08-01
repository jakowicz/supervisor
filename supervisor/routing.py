"""Deterministic routing policy: no worker can silently approve its own result."""

from __future__ import annotations

import os

from .models import Status, WorkerResult


# Limits are total attempts for implementation agents.  The Codex-final value
# is deliberately a *post-QA repair* budget: its initial verification runs
# once, then it may repair demonstrated QA failures up to this many times.
DEFAULT_AGENT_LIMITS = {"qwen": 1, "openhands": 1, "codex": 3, "codex_final": 3}
_LIMIT_ENVIRONMENT_KEYS = {
    "qwen": "SUPERVISOR_QWEN_ATTEMPTS",
    "openhands": "SUPERVISOR_OPENHANDS_ATTEMPTS",
    "codex": "SUPERVISOR_CODEX_ATTEMPTS",
    "codex_final": "SUPERVISOR_CODEX_FINAL_ATTEMPTS",
}


def agent_limits() -> dict[str, int]:
    """Load bounded retry policy from project configuration."""

    limits = dict(DEFAULT_AGENT_LIMITS)
    for agent, key in _LIMIT_ENVIRONMENT_KEYS.items():
        try:
            configured = int(os.getenv(key, str(limits[agent])))
        except ValueError:
            configured = limits[agent]
        limits[agent] = max(configured, 1)
    return limits


def next_agent(current_agent: str, attempts: dict[str, int]) -> str:
    """Keep a repair with its current agent until its bounded budget is spent."""

    limit = agent_limits()[current_agent]
    # The first Codex-final pass is an independent verification, not a repair.
    # Reserve the configured budget for retries caused by a failing QA stage.
    if current_agent == "codex_final":
        limit += 1
    if attempts.get(current_agent, 0) < limit:
        return current_agent
    return {
        "qwen": "openhands",
        "openhands": "codex",
        "codex": "user_review",
        "codex_final": "user_review",
    }[current_agent]


def next_route(stage: str, result: WorkerResult, active_agent: str, attempts: dict[str, int]) -> str:
    """Return a graph node name from evidence and bounded retry counts."""

    if result.status is Status.PASS:
        return {
            # A successful primary/fallback implementation is always examined
            # and, where necessary, repaired by Codex before acceptance.  A
            # deterministic precheck comes first: do not spend the final
            # review budget on a candidate that does not even compile.
            "qwen": "precheck",
            "openhands": "precheck",
            # Codex already performed the implementation when it was the
            # fallback, so a second Codex pass would be redundant.
            "codex": "test",
            "precheck": "codex_final",
            "codex_final": "test",
            "test": "browser",
            "browser": "visual_review",
            "visual_review": "completion_audit",
            "completion_audit": "git_publish",
            "git_publish": "accept",
        }[stage]
    if result.status is Status.NEEDS_USER_REVIEW:
        return "user_review"
    # Validation infrastructure is not a coding-agent defect. Preserve evidence
    # and ask for repair/configuration rather than burning expensive retries.
    if result.status is Status.ENVIRONMENT_FAILURE and stage in {"precheck", "test", "browser", "visual_review"}:
        return "user_review"
    if stage in {"precheck", "test", "browser", "visual_review", "completion_audit"}:
        return next_agent(active_agent, attempts)
    return next_agent(stage, attempts)
