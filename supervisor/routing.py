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
_IMPLEMENTATION_AGENTS = ("qwen", "openhands", "codex")
_ART_STAGES = ("art_director", "asset_generator", "asset_finisher", "asset_qa")
_ALL_STAGES = (*_ART_STAGES, "qwen", "openhands", "codex", "precheck", "codex_final", "test", "browser", "visual_review", "completion_audit", "git_publish")


def asset_pipeline_required(task) -> bool:
    """True only for runbooks which explicitly request generated visual assets."""

    return bool(task and task.asset_impact.strip().lower() == "required")


def implementation_agents() -> tuple[str, ...]:
    """Return the project-selected implementation agents in execution order."""

    configured = os.getenv("SUPERVISOR_CODING_AGENTS", ",".join(_IMPLEMENTATION_AGENTS))
    agents = tuple(agent.strip().lower() for agent in configured.split(",") if agent.strip().lower() in _IMPLEMENTATION_AGENTS)
    return agents or _IMPLEMENTATION_AGENTS


def primary_agent() -> str:
    return implementation_agents()[0]


def configured_stage_order() -> tuple[str, ...]:
    """Return only an explicit pipeline that preserves every acceptance gate.

    This compatibility setting used to accept partial lists.  A resumed worker
    missing from such a list was treated as the final stage and could reach
    ``accept`` directly.  Configuration must never weaken the mandatory QA
    chain, so partial/stale values now fall back to the safe default routing.
    """

    configured = os.getenv("SUPERVISOR_AGENT_ORDER", "")
    stages = tuple(stage.strip().lower() for stage in configured.split(",") if stage.strip().lower() in _ALL_STAGES)
    if not stages:
        return ()

    primary = primary_agent()
    verification_stages = ("test", "browser", "visual_review", "completion_audit", "git_publish")
    required = (
        (primary, *verification_stages)
        if primary == "codex" or "codex" not in implementation_agents()
        else (primary, "precheck", "codex_final", *verification_stages)
    )
    return stages if stages == required else ()


def first_stage(task=None) -> str:
    if asset_pipeline_required(task):
        return "art_director"
    return configured_stage_order()[0] if configured_stage_order() else primary_agent()


def next_configured_stage(stage: str) -> str:
    stages = configured_stage_order()
    try:
        return stages[stages.index(stage) + 1]
    except (ValueError, IndexError):
        return "accept"


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
    if current_agent in _IMPLEMENTATION_AGENTS:
        agents = implementation_agents()
        try:
            return agents[agents.index(current_agent) + 1]
        except (ValueError, IndexError):
            return "user_review"
    return "user_review"


def next_route(stage: str, result: WorkerResult, active_agent: str, attempts: dict[str, int], task=None) -> str:
    """Return a graph node name from evidence and bounded retry counts."""

    if result.status is Status.PASS:
        # Art stages always use the bounded, evidence-gated art lane.  A
        # legacy configured code pipeline must never make an art stage jump
        # directly to acceptance.
        if configured_stage_order() and stage not in _ART_STAGES:
            return next_configured_stage(stage)
        routes = {
            "art_director": "asset_generator",
            "asset_generator": "asset_finisher",
            "asset_finisher": "asset_qa",
            "asset_qa": primary_agent(),
            # A successful primary/fallback implementation is always examined
            # and, where necessary, repaired by Codex before acceptance.  A
            # deterministic precheck comes first: do not spend the final
            # review budget on a candidate that does not even compile.
            "qwen": "precheck" if "codex" in implementation_agents() else "test",
            "openhands": "precheck" if "codex" in implementation_agents() else "test",
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
        }
        return routes[stage]
    if result.status is Status.NEEDS_USER_REVIEW:
        return "user_review"
    # Asset creation has no coding-agent fallback: blindly changing a visual
    # brief or regenerating art after a provenance/technical rejection would
    # hide the reason an asset was rejected. Preserve evidence and stop.
    if stage in _ART_STAGES:
        return "user_review"
    # Validation infrastructure is not a coding-agent defect. Preserve evidence
    # and ask for repair/configuration rather than burning expensive retries.
    if result.status is Status.ENVIRONMENT_FAILURE and stage in {"precheck", "test", "browser", "visual_review"}:
        return "user_review"
    if stage in {"precheck", "test", "browser", "visual_review", "completion_audit"}:
        return next_agent(active_agent, attempts)
    return next_agent(stage, attempts)
