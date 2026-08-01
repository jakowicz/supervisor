"""Command-line entry point for one scoped supervisor run."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .graph import SupervisorConfig, create_graph
from .checkpoints import continuation_brief, diff_snapshot, stream_checkpoint, stream_delta
from .models import RunEvent, Status, Task, TaskRun, WorkerResult, model_to_dict
from .observability import SupervisorTelemetry
from .recovery import latest_qwen_result, qwen_logs
from .runbooks import load_task
from .storage import RunStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one evidence-gated supervisor task.")
    parser.add_argument("--task-id", help="Runbook task ID, for example D005. Automatically loads runbooks/<ID>.md when present.")
    parser.add_argument("--task-range", help="Sequential runbook range, for example D007-D010. Stops at the first task that is not accepted.")
    parser.add_argument("--continue-on-nonpass", action="store_true", help="Continue a task range after a task needs review or fails. Use only when tasks are independent.")
    parser.add_argument("--runbook", help="Path to a single task runbook, relative to the repository or absolute.")
    parser.add_argument("--title")
    parser.add_argument("--objective", default="")
    parser.add_argument("--acceptance", action="append", default=[], help="Repeat once per acceptance criterion.")
    parser.add_argument("--sequence", type=int, default=None, help="Task sequence; every fifth task runs the full browser suite.")
    parser.add_argument("--browser-impact", choices=["required", "not_applicable"], default=None)
    parser.add_argument("--playwright-spec", action="append", default=[], help="Task-specific spec relative to browser/, repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Restart an unfinished or needs-review task at the primary Qwen stage while preserving its worktree and durable history.",
    )
    parser.add_argument(
        "--output-format",
        choices=["summary", "json"],
        default="summary",
        help="Terminal completion output. Full raw evidence remains in SQLite and live logs.",
    )
    arguments = parser.parse_args()
    load_dotenv()
    package_root = Path(__file__).resolve().parents[1]
    repo_root = Path(os.getenv("SUPERVISOR_REPO_ROOT", package_root.parents[1])).resolve()
    database_path = Path(os.getenv("SUPERVISOR_DATABASE_PATH", package_root / ".state" / "supervisor.sqlite3"))
    if arguments.task_range:
        if any((arguments.task_id, arguments.runbook, arguments.title, arguments.objective, arguments.acceptance)):
            parser.error("--task-range cannot be combined with a single-task runbook or ad-hoc task options.")
        try:
            task_ids = _expand_task_range(arguments.task_range)
        except ValueError as error:
            parser.error(str(error))
        runbooks = [repo_root / "runbooks" / f"{task_id}.md" for task_id in task_ids]
        missing = [str(path) for path in runbooks if not path.is_file()]
        if missing:
            parser.error("Task range requires an installed runbook for every task: " + ", ".join(missing))
        _run_task_range(runbooks, arguments.dry_run, arguments.continue_on_nonpass, database_path)
        return
    if arguments.runbook:
        # An explicit path follows normal CLI behaviour: relative to the
        # current directory first. Repository-relative is a convenient fallback.
        supplied_path = Path(arguments.runbook).expanduser()
        requested_runbook = supplied_path if supplied_path.is_absolute() else supplied_path.resolve()
        if not requested_runbook.is_file():
            requested_runbook = repo_root / supplied_path
    else:
        requested_runbook = repo_root / "runbooks" / f"{arguments.task_id.upper()}.md" if arguments.task_id else None
    if requested_runbook and requested_runbook.is_file():
        task = load_task(requested_runbook)
    else:
        if not arguments.task_id or not arguments.title:
            parser.error("Provide --task-id for an installed runbook, --runbook PATH, or both --task-id and --title for an ad-hoc task.")
        task = Task(task_id=arguments.task_id, title=arguments.title, objective=arguments.objective, acceptance_criteria=arguments.acceptance, sequence=arguments.sequence or 0, browser_impact=arguments.browser_impact or "not_applicable", playwright_specs=arguments.playwright_spec)
    store = RunStore(database_path)
    previous_state = store.state_for(task.task_id)
    # An accepted task is immutable from the supervisor's perspective.  A
    # caller can intentionally make a new task/runbook when scope changes;
    # blindly sending an already accepted task back to a slow coding model is
    # both wasteful and risky.
    if previous_state and previous_state.get("status") == "accepted":
        accepted_commit = previous_state.get("accepted_commit")
        if accepted_commit and _commit_exists(repo_root, accepted_commit):
            print(
                f"{task.task_id} is already accepted at {accepted_commit[:12]}; no agent was started. "
                "Create a new task ID for additional scope.",
                file=sys.stderr,
            )
            store.close()
            return
    run_id = str(uuid.uuid4())
    previous_state = store.claim_task(task.task_id, run_id, os.getpid())
    previous_qwen_session = None if arguments.retry else _qwen_session_to_resume(previous_state)
    if previous_qwen_session:
        # The session identifier is persisted at every Qwen stream checkpoint.
        # Restoring it here lets a fresh supervisor invocation continue the
        # same Qwen conversation rather than merely handing a summary to a new
        # one. The adapter consumes this value as `qwen --resume <id>`.
        os.environ["SUPERVISOR_QWEN_RESUME_SESSION_ID"] = previous_qwen_session
    continuation = continuation_brief(previous_state)
    if continuation:
        task = task.model_copy(update={"continuation_context": continuation})
    run_number = store.next_task_run_number(task.task_id)
    # Labels make local evidence unambiguous when several tasks and retries
    # are visible together in Finder, a terminal, or the dashboard.
    live_log_path = database_path.parent / "live" / (
        f"task-{task.task_id.lower()}-run-{run_number:02d}-stage-00-agent-supervisor-{run_id}.log"
    )
    live_log_path.parent.mkdir(parents=True, exist_ok=True)
    recovered_qwen = None
    if _can_recover_qwen(previous_state):
        recovered_qwen = latest_qwen_result(database_path.parent / "live", task.task_id)
    if recovered_qwen and not arguments.retry:
        newest_qwen_log = qwen_logs(database_path.parent / "live", task.task_id)[0]
        handoff = (
            "Recovery evidence: a completed Qwen result was recovered from "
            f"{recovered_qwen[1].name}. "
            "The current worktree includes all changes from that run and any "
            "later partial attempts. Preserve those changes; validate the "
            "combined worktree first and repair only a demonstrated failure."
        )
        if newest_qwen_log != recovered_qwen[1]:
            handoff += f" A newer incomplete Qwen log also exists: {newest_qwen_log.name}."
        task = task.model_copy(update={
            "continuation_context": "\n".join(part for part in (task.continuation_context, handoff) if part),
        })

    def progress(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()}  {message}"
        print(line, file=sys.stderr, flush=True)
        with live_log_path.open("a", encoding="utf-8") as live_log:
            live_log.write(line + "\n")

    def stage_log_path(stage: str, stage_number: int) -> Path:
        return live_log_path.with_name(
            f"task-{task.task_id.lower()}-run-{run_number:02d}-stage-{stage_number:02d}-"
            f"agent-{stage.replace('_', '-')}-{run_id}.log"
        )

    def event_log(stage: str, result: WorkerResult, stage_number: int) -> None:
        path = stage_log_path(stage, stage_number)
        content = [
            f"Task: {task.task_id} — {task.title}",
            f"Stage: {stage}",
            f"Status: {result.status.value}",
            f"Summary: {result.summary}",
        ]
        for label, value in (
            ("Raw agent output", result.evidence.agent_log),
            ("Adapter output", result.evidence.adapter_log),
            ("Test output", result.evidence.test_log),
            ("Browser output", result.evidence.browser_log),
        ):
            if value:
                content.extend(["", f"===== {label} =====", value])
        audit = "\n".join(content) + "\n"
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if existing:
            path.write_text(existing + "\n===== SUPERVISOR RESULT =====\n" + audit, encoding="utf-8")
        else:
            path.write_text(audit, encoding="utf-8")
        progress(f"EVENT {stage} · full output saved to {path}")

    stream_offsets: dict[Path, int] = {}
    stream_evidence_offsets: dict[Path, int] = {}
    live_event_counts: dict[str, int] = {}

    def checkpoint(
        stage: str,
        agent: str,
        stage_number: int,
        stream_path: Path | None,
        result: WorkerResult | None,
        route: str | None,
    ) -> None:
        """Persist a tiny handoff packet on stage start, heartbeat, and exit."""

        payload: dict[str, object] = {}
        if stream_path and stage == "qwen":
            prior_stream_offset = stream_offsets.get(stream_path)
            extracted, stream_offsets[stream_path] = stream_checkpoint(
                stream_path, stream_offsets.get(stream_path, 0)
            )
            if extracted:
                payload.update({key: value for key, value in extracted.items() if value is not None})
            elif prior_stream_offset is None:
                # Record a useful initial state, but do not overwrite a
                # meaningful tool/session checkpoint during later silence.
                payload.update({
                    "summary": f"{agent} is working in stage {stage}.",
                    "next_action": f"continue_{stage}",
                })
            # Make a just-discovered Qwen session available to a retry in this
            # same supervisor process as well as to a later resumed run.
            if payload.get("session_id"):
                os.environ["SUPERVISOR_QWEN_RESUME_SESSION_ID"] = str(payload["session_id"])
            delta, stream_evidence_offsets[stream_path] = stream_delta(
                stream_path, stream_evidence_offsets.get(stream_path, 0)
            )
            if delta:
                payload["stream_excerpt"] = delta
        else:
            payload.update({
                "summary": f"{agent} is working in stage {stage}.",
                "next_action": f"continue_{stage}",
            })
        if stage in {"qwen", "openhands", "codex", "codex_final"}:
            payload.update(diff_snapshot(repo_root))
        if result is not None:
            payload.update({"summary": result.summary, "next_action": route or stage})
        store_payload = {key: value for key, value in payload.items() if key != "stream_excerpt"}
        store.checkpoint(run_id, task.task_id, stage, agent, "stage_complete" if result else "heartbeat", store_payload)
        # Emit short, closed events as Qwen produces output. They are visible
        # immediately in Langfuse, unlike the enclosing long-running span.
        if telemetry.is_enabled and (result is not None or payload.get("stream_excerpt")):
            live_event_counts[stage] = live_event_counts.get(stage, 0) + 1
            telemetry.live_checkpoint(task, run_id, stage, agent, live_event_counts[stage], payload)

    progress(f"RUN   {task.task_id.lower()}-{run_number:02d} · {run_id} · {task.title}")
    if continuation:
        progress(f"RESUME {task.task_id} · restored durable checkpoint for {previous_state.get('status', 'unknown')}")
    if previous_qwen_session:
        progress(f"RESUME {task.task_id} · restoring Qwen session {previous_qwen_session}")
    if recovered_qwen:
        recovered_result, recovered_path = recovered_qwen
        progress(f"RECOVER qwen · parsed prior {recovered_result.status.value} result from {recovered_path.name}")
    progress(f"LOG   {live_log_path}")
    heartbeat_seconds = int(os.getenv("SUPERVISOR_PROGRESS_HEARTBEAT_SECONDS", "30"))
    telemetry = SupervisorTelemetry.from_environment()
    final_state: dict | None = None
    recovered_result = recovered_qwen[0] if recovered_qwen else None
    recovered_event = (
        _recovered_qwen_event(recovered_result)
        if recovered_result and recovered_result.status is Status.PASS
        else None
    )
    initial_events = [recovered_event] if recovered_event else []
    initial_results = [recovered_result] if recovered_event else []
    initial_attempts = {"qwen": 1} if recovered_event else {}
    resume_stage = "qwen" if arguments.retry else ("codex_final" if recovered_event else _resume_stage(previous_state))
    try:
        with telemetry.run(task, run_id, run_number) as run_span:
            final_state = create_graph(SupervisorConfig(repo_root=repo_root, dry_run=arguments.dry_run, progress=progress, event_log=event_log, stage_log_path=stage_log_path, checkpoint=checkpoint, progress_heartbeat_seconds=heartbeat_seconds, telemetry=telemetry)).invoke({"task": task, "run_id": run_id, "worker_results": initial_results, "events": initial_events, "attempts": initial_attempts, "active_agent": "qwen", "notes": ["Recovered prior Qwen result; starting independent validation."] if recovered_event else [], "resume_stage": resume_stage})
            telemetry.complete_run(run_span, final_state["final_status"], final_state["route"], len(final_state["events"]))
    except BaseException as error:
        store.abandon_task(task.task_id, run_id, f"Interrupted during supervisor run: {type(error).__name__}: {error}")
        progress(f"INTERRUPTED {task.task_id} · durable checkpoint retained · {type(error).__name__}: {error}")
        raise
    finally:
        telemetry.flush()
        os.environ.pop("SUPERVISOR_QWEN_RESUME_SESSION_ID", None)
    assert final_state is not None
    run = TaskRun(
        run_id=run_id,
        task=task,
        status=Status(final_state["final_status"]),
        route=final_state["route"],
        worker_results=final_state["worker_results"],
        events=final_state["events"],
        notes=final_state["notes"],
    )
    try:
        store.save(run)
        store.finish_task(run, _head_commit(repo_root) if run.status is Status.PASS else None)
    finally:
        store.close()
    progress(f"FINAL {run.status.value} · report stored in SQLite and {database_path.parent / 'reports' / f'{run_id}.md'}")
    if arguments.output_format == "json":
        print(json.dumps(model_to_dict(run), indent=2))
    else:
        print(_run_summary(run, database_path))


def _commit_exists(repo_root: Path, commit: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _run_summary(run: TaskRun, database_path: Path) -> str:
    """Render a compact terminal handoff without duplicating raw evidence."""

    report_path = database_path.parent / "reports" / f"{run.run_id}.md"
    lines = [
        f"{run.task.task_id} · {run.status.value} · {run.route}",
        f"Run: {run.run_id}",
        "Stages:",
    ]
    lines.extend(
        f"- {event.stage} ({event.agent}) · {event.status.value} → {event.route} · {event.summary}"
        for event in run.events
    )
    lines.extend([
        f"Report: {report_path}",
        f"Evidence: {database_path}",
        f"Inspect: supervisor-reports show {run.run_id}",
        "Use --output-format json only when another program needs the full raw run payload.",
    ])
    return "\n".join(lines)


def _can_recover_qwen(state: dict | None) -> bool:
    """Accept valid Qwen evidence from any unfinished, no-longer-live run.

    A supervisor process can fail before its outer exception handler is active.
    In that case the prior task remains marked ``implementing`` even though its
    Qwen stage already wrote a complete result to the durable live log.
    ``claim_task`` has already rejected a truly live owner before this point,
    so all unfinished statuses are safe recovery candidates here.
    """

    return bool(state and state.get("status") in {"interrupted", "implementing", "validating"})


def _recovered_qwen_event(result: WorkerResult) -> RunEvent:
    """Record a recovered Qwen pass and route it through Codex final review."""

    return RunEvent(
        stage="qwen",
        agent="Qwen3 Coder (recovered)",
        model="QWEN_MODEL prior session",
        attempt=1,
        status=result.status,
        summary=result.summary,
        route="codex_final",
        result=result,
    )


def _head_commit(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _resume_stage(state: dict | None) -> str:
    """Choose only a graph node that can safely continue a prior run."""

    if not state or state.get("status") not in {"interrupted", "implementing", "validating"}:
        return "prepare"
    candidate = str(state.get("next_action", "prepare"))
    return candidate if candidate in {
        "prepare", "qwen", "openhands", "codex", "codex_final", "test", "browser",
        "visual_review", "completion_audit", "git_publish", "user_review",
    } else "prepare"


def _qwen_session_to_resume(state: dict | None) -> str | None:
    """Return a durable Qwen session ID only for an unfinished task."""

    if not state or state.get("status") not in {"interrupted", "implementing", "validating"}:
        return None
    session_id = state.get("agent_session_id")
    return str(session_id) if session_id else None


def _expand_task_range(value: str) -> list[str]:
    """Validate and expand an inclusive, ascending runbook range."""

    match = re.fullmatch(r"([A-Za-z]+)(\d+)-([A-Za-z]+)(\d+)", value.strip())
    if not match:
        raise ValueError("Task range must look like D007-D010.")
    start_prefix, start_number, end_prefix, end_number = match.groups()
    if start_prefix.upper() != end_prefix.upper() or int(start_number) > int(end_number):
        raise ValueError("Task range must use one prefix and ascend, for example D007-D010.")
    width = max(len(start_number), len(end_number))
    return [f"{start_prefix.upper()}{number:0{width}d}" for number in range(int(start_number), int(end_number) + 1)]


def _run_task_range(
    runbooks: list[Path], dry_run: bool, continue_on_nonpass: bool, database_path: Path
) -> None:
    """Run independent CLI invocations sequentially in one shared worktree."""

    print(
        "BATCH START " + ", ".join(path.stem for path in runbooks) + " · sequential shared-worktree mode",
        file=sys.stderr,
        flush=True,
    )
    for index, runbook in enumerate(runbooks, start=1):
        task_id = runbook.stem
        print(f"BATCH {index:02d}/{len(runbooks):02d} START {task_id} · {runbook}", file=sys.stderr, flush=True)
        command = [sys.executable, "-m", "supervisor.cli", "--runbook", str(runbook)]
        if dry_run:
            command.append("--dry-run")
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            raise SystemExit(f"BATCH HALTED {task_id} · supervisor process exited {completed.returncode}")
        store = RunStore(database_path)
        try:
            state = store.state_for(task_id)
        finally:
            store.close()
        status = state.get("status") if state else "missing_state"
        if status == "accepted":
            print(f"BATCH {index:02d}/{len(runbooks):02d} DONE {task_id} · accepted", file=sys.stderr, flush=True)
            continue
        message = f"BATCH {index:02d}/{len(runbooks):02d} STOP {task_id} · state={status}"
        if not continue_on_nonpass:
            print(message + " · later tasks were not started", file=sys.stderr, flush=True)
            return
        print(message + " · continuing by explicit override", file=sys.stderr, flush=True)
    print("BATCH FINAL · all requested tasks were started", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
