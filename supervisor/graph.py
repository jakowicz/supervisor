"""Executable LangGraph supervisor with evidence-gated routing."""

from __future__ import annotations

from dataclasses import dataclass
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from langgraph.graph import END, START, StateGraph

from .models import AcceptanceResult, CriterionStatus, DocumentationReport, NextStep, RunEvent, Status, Task, WorkerResult
from .completion_audit import audit
from .git_publish import preflight, publish
from .routing import first_stage, next_route
from .state import SupervisorState
from .workers import art_director, asset_finisher, asset_generator, asset_qa, browser, codex, coder, openhands, tester, visual_review
from .observability import SupervisorTelemetry


@dataclass(frozen=True)
class SupervisorConfig:
    repo_root: Path
    dry_run: bool = False
    progress: Callable[[str], None] | None = None
    event_log: Callable[[str, WorkerResult, int], None] | None = None
    stage_log_path: Callable[[str, int], Path] | None = None
    # Called from the supervisor thread (never the worker thread) whenever a
    # stage makes observable progress.  This keeps continuation state durable
    # even if the terminal, CLI, or model process is interrupted.
    checkpoint: Callable[[str, str, int, Path | None, WorkerResult | None, str | None], None] | None = None
    progress_heartbeat_seconds: int = 30
    telemetry: SupervisorTelemetry | None = None


def _dry_run_coder(task: Task) -> WorkerResult:
    return WorkerResult(
        status=Status.PASS,
        summary=f"Dry-run coder completed {task.task_id}.",
        test_result="Dry-run coding checks passed.",
        acceptance_results=[AcceptanceResult(criterion=criterion, status=CriterionStatus.PASS, evidence="Dry-run evidence.") for criterion in task.acceptance_criteria],
        documentation=DocumentationReport(reviewed_files=["README.md", "PROJECT_PLAN.md"], summary="Dry-run documentation review."),
        recommended_next_step=NextStep.COMPLETE,
    )


def _repair_handoff(stage: str, result: WorkerResult) -> str:
    """Return the most useful bounded evidence packet for a coding repair."""

    if stage == "browser" and result.evidence.browser_log:
        return (
            "Browser QA executed and failed. Treat this as a product/test failure, "
            "not an environment failure, unless the raw log proves that no browser "
            "test could start. Repair the failing test or product behavior, then let "
            "independent QA rerun it.\n\n"
            + result.evidence.browser_log[-12000:]
        )
    evidence = (
        result.test_result
        or result.evidence.test_log
        or result.evidence.agent_log
        or result.evidence.adapter_log
        or result.summary
    )
    return evidence[-12000:]


def create_graph(config: SupervisorConfig):
    builder = StateGraph(SupervisorState)

    def worker_node(stage: str, agent: str, model: str, runner: Callable[[Task], WorkerResult]):
        def node(state: SupervisorState) -> dict:
            task = state["task"]
            # When deterministic QA sends work back to a coding agent, give it
            # the concrete failure rather than asking it to rediscover the
            # problem from a large worktree.  This is especially important for
            # Flutter failures, which the final Codex review intentionally
            # leaves to the independent test stage.
            if stage in {"qwen", "openhands", "codex", "codex_final"} and state.get("events"):
                prior = state["events"][-1]
                if prior.stage in {"precheck", "test", "browser", "visual_review", "completion_audit"} and prior.status is not Status.PASS:
                    evidence = _repair_handoff(prior.stage, prior.result)
                    handoff = f"Previous {prior.stage} stage failed; repair this evidence before retry:\n{evidence}"
                    task = task.model_copy(update={
                        "continuation_context": "\n".join(part for part in (task.continuation_context, handoff) if part),
                    })
            if stage == "codex_final":
                task = task.model_copy(update={"execution_mode": "final_verification"})
            stage_number = len(state.get("events", [])) + 1
            if config.progress:
                config.progress(f"START {stage} · {agent}")
            # Coding CLIs can legitimately be silent during local model
            # inference. Keep the terminal/log useful without consuming their
            # stdout, which remains evidence for the final worker result.
            previous_stream_log = os.environ.get("SUPERVISOR_STREAM_LOG")
            if config.stage_log_path:
                os.environ["SUPERVISOR_STREAM_LOG"] = str(config.stage_log_path(stage, stage_number))
            stream_path = config.stage_log_path(stage, stage_number) if config.stage_log_path else None
            if config.checkpoint:
                config.checkpoint(stage, agent, stage_number, stream_path, None, None)
            telemetry = config.telemetry
            run_id = state.get("run_id", "")
            attempt = state.get("attempts", {}).get(stage, 0) + (1 if stage in {"qwen", "openhands", "codex", "codex_final"} else 0)
            stage_context = telemetry.stage(task, run_id, stage, agent, model, attempt) if telemetry else None
            stage_span = stage_context.__enter__() if stage_context else None
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(runner, task)
                    while True:
                        try:
                            result = future.result(timeout=config.progress_heartbeat_seconds)
                            break
                        except TimeoutError:
                            if config.checkpoint:
                                config.checkpoint(stage, agent, stage_number, stream_path, None, None)
                            if config.progress:
                                config.progress(f"WAIT  {stage} · {agent} is still running")
            except BaseException as error:
                if stage_context:
                    stage_context.__exit__(type(error), error, error.__traceback__)
                raise
            finally:
                if previous_stream_log is None:
                    os.environ.pop("SUPERVISOR_STREAM_LOG", None)
                else:
                    os.environ["SUPERVISOR_STREAM_LOG"] = previous_stream_log
            attempts = dict(state.get("attempts", {}))
            # Only coding attempts consume the Qwen/OpenHands/Codex retry budget.
            if stage in {"qwen", "openhands", "codex", "codex_final"}:
                attempts[stage] = attempts.get(stage, 0) + 1
            route = next_route(stage, result, state.get("active_agent", agent), attempts, task)
            if telemetry:
                telemetry.complete_stage(stage_span, result, route)
            if stage_context:
                stage_context.__exit__(None, None, None)
            event = RunEvent(
                stage=stage,
                agent=agent,
                model=model,
                attempt=attempts.get(stage, attempts.get(state.get("active_agent", agent), 0)),
                status=result.status,
                summary=result.summary,
                route=route,
                result=result,
            )
            if config.event_log:
                config.event_log(stage, result, stage_number)
            if config.checkpoint:
                config.checkpoint(stage, agent, stage_number, stream_path, result, route)
            if config.progress:
                config.progress(f"DONE  {stage} · {result.status.value} → {route} · {result.summary}")
            return {
                "worker_results": [*state.get("worker_results", []), result],
                "events": [*state.get("events", []), event],
                "attempts": attempts,
                "active_agent": stage if stage in {"qwen", "openhands", "codex", "codex_final"} else state.get("active_agent", agent),
                "route": route,
                "notes": [*state.get("notes", []), f"{stage} / {agent} / {result.status.value}: {result.summary}"],
            }
        return node

    def prepare_node(state: SupervisorState) -> dict:
        if config.progress:
            config.progress("START prepare · Git baseline guard")
        if config.checkpoint:
            config.checkpoint("prepare", "Git baseline guard", len(state.get("events", [])) + 1, None, None, None)
        context = config.telemetry.stage(state["task"], state.get("run_id", ""), "prepare", "Git baseline guard", "git", 0) if config.telemetry else None
        with (context or nullcontext(None)) as span:
            result = preflight(state["task"], config.repo_root)
            route = first_stage(state["task"]) if result.status is Status.PASS else "user_review"
            if config.telemetry:
                config.telemetry.complete_stage(span, result, route)
        event = RunEvent(stage="prepare", agent="Git baseline guard", model="git", attempt=0, status=result.status, summary=result.summary, route=route, result=result)
        if config.event_log:
            config.event_log("prepare", result, len(state.get("events", [])) + 1)
        if config.checkpoint:
            config.checkpoint("prepare", "Git baseline guard", len(state.get("events", [])) + 1, None, result, route)
        if config.progress:
            config.progress(f"DONE  prepare · {result.status.value} → {route} · {result.summary}")
        return {
            "worker_results": [*state.get("worker_results", []), result],
            "events": [*state.get("events", []), event],
            "route": route,
            "active_agent": first_stage(state["task"]),
            "notes": [*state.get("notes", []), f"prepare: {result.summary}"],
        }

    builder.add_node("prepare", prepare_node)
    builder.add_node("art_director", worker_node("art_director", "project art director", "structured local brief", lambda task: art_director.run(task, config.repo_root)))
    builder.add_node("asset_generator", worker_node("asset_generator", "ComfyUI Z-Image Turbo", "local ComfyUI", lambda task: asset_generator.run(task, config.repo_root)))
    builder.add_node("asset_finisher", worker_node("asset_finisher", "asset finisher", "deterministic local processing", lambda task: asset_finisher.run(task, config.repo_root)))
    builder.add_node("asset_qa", worker_node("asset_qa", "asset QA reviewer", "local technical and provenance checks", lambda task: asset_qa.run(task, config.repo_root)))
    builder.add_node("qwen", worker_node("qwen", "Qwen3 Coder", "QWEN_MODEL or CLI default", _dry_run_coder if config.dry_run else coder.run))
    builder.add_node("openhands", worker_node("openhands", "OpenHands", "OPENHANDS configured model", openhands.run))
    builder.add_node("codex", worker_node("codex", "Codex", "CODEX_MODEL or CLI default", codex.run))
    builder.add_node("codex_final", worker_node("codex_final", "Codex final verifier/fixer", "CODEX_MODEL or CLI default", _dry_run_coder if config.dry_run else codex.run))
    builder.add_node("precheck", worker_node("precheck", "candidate Flutter precheck", "deterministic shell commands", lambda task: tester.run(task, config.repo_root, config.dry_run)))
    builder.add_node("test", worker_node("test", "independent Flutter test worker", "deterministic shell commands", lambda task: tester.run(task, config.repo_root, config.dry_run)))
    builder.add_node("browser", worker_node("browser", "browser QA worker", "BROWSER_QA_COMMAND", lambda task: browser.run(task, config.dry_run)))
    builder.add_node("visual_review", worker_node("visual_review", "visual QA reviewer", "VISUAL_REVIEW_COMMAND", lambda task: visual_review.run(task, config.dry_run)))
    def publish_node(state: SupervisorState) -> dict:
        if config.progress:
            config.progress("START git_publish · Git publisher")
        if config.checkpoint:
            config.checkpoint("git_publish", "Git publisher", len(state.get("events", [])) + 1, None, None, None)
        context = config.telemetry.stage(state["task"], state.get("run_id", ""), "git_publish", "Git publisher", "git", 0) if config.telemetry else None
        with (context or nullcontext(None)) as span:
            result = publish(state["task"], config.repo_root)
            route = next_route("git_publish", result, state.get("active_agent", "qwen"), state.get("attempts", {}), state["task"])
            if config.telemetry:
                config.telemetry.complete_stage(span, result, route)
        event = RunEvent(stage="git_publish", agent="Git publisher", model="git", attempt=0, status=result.status, summary=result.summary, route=route, result=result)
        if config.event_log:
            config.event_log("git_publish", result, len(state.get("events", [])) + 1)
        if config.checkpoint:
            config.checkpoint("git_publish", "Git publisher", len(state.get("events", [])) + 1, None, result, route)
        if config.progress:
            config.progress(f"DONE  git_publish · {result.status.value} → {route} · {result.summary}")
        return {"worker_results": [*state.get("worker_results", []), result], "events": [*state.get("events", []), event], "route": route, "notes": [*state.get("notes", []), f"git publish: {result.status.value}: {result.summary}"]}
    builder.add_node("git_publish", publish_node)
    builder.add_node("accept", lambda state: {"final_status": Status.PASS.value, "route": "accepted"})
    builder.add_node("user_review", lambda state: {"final_status": Status.NEEDS_USER_REVIEW.value, "route": "needs_user_review"})
    # Start a recovered invocation at its durable next stage rather than
    # re-running a slow agent that already completed successfully.
    builder.add_conditional_edges(START, lambda state: state.get("resume_stage", "prepare"))
    builder.add_conditional_edges("prepare", lambda state: state["route"])
    # The audit needs all preceding agent events, unlike ordinary workers.
    def audit_node(state: SupervisorState) -> dict:
        if config.progress:
            config.progress("START completion_audit · completion-contract auditor")
        if config.checkpoint:
            config.checkpoint("completion_audit", "completion-contract auditor", len(state.get("events", [])) + 1, None, None, None)
        attempts = dict(state.get("attempts", {}))
        context = config.telemetry.stage(state["task"], state.get("run_id", ""), "completion_audit", "completion-contract auditor", "deterministic policy", attempts.get(state.get("active_agent", "qwen"), 0)) if config.telemetry else None
        with (context or nullcontext(None)) as span:
            result = audit(state["task"], state.get("events", []))
            route = next_route("completion_audit", result, state.get("active_agent", "qwen"), attempts, state["task"])
            if config.telemetry:
                config.telemetry.complete_stage(span, result, route)
        event = RunEvent(stage="completion_audit", agent="completion-contract auditor", model="deterministic policy", attempt=attempts.get(state.get("active_agent", "qwen"), 0), status=result.status, summary=result.summary, route=route, result=result)
        if config.event_log:
            config.event_log("completion_audit", result, len(state.get("events", [])) + 1)
        if config.checkpoint:
            config.checkpoint("completion_audit", "completion-contract auditor", len(state.get("events", [])) + 1, None, result, route)
        if config.progress:
            config.progress(f"DONE  completion_audit · {result.status.value} → {route} · {result.summary}")
        return {"worker_results": [*state.get("worker_results", []), result], "events": [*state.get("events", []), event], "route": route, "notes": [*state.get("notes", []), f"completion audit: {result.status.value}: {result.summary}"]}

    builder.add_node("completion_audit", audit_node)
    for stage in ("art_director", "asset_generator", "asset_finisher", "asset_qa", "qwen", "openhands", "codex", "codex_final", "precheck", "test", "browser", "visual_review", "completion_audit", "git_publish"):
        builder.add_conditional_edges(stage, lambda state: state["route"])
    builder.add_edge("accept", END)
    builder.add_edge("user_review", END)
    return builder.compile()
