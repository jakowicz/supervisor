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

from .graph import SupervisorConfig, create_graph
from .environment import load_project_environment, project_path
from .checkpoints import continuation_brief, diff_snapshot, stream_checkpoint, stream_delta
from .failure_summary import summarize_failure
from .models import RunEvent, Status, Task, TaskRun, WorkerResult, model_to_dict
from .observability import SupervisorTelemetry
from .recovery import latest_qwen_result, qwen_logs
from .runbooks import load_task
from .storage import RunStore


START_STAGES = (
    "prepare", "art_director", "asset_generator", "asset_finisher", "asset_qa",
    "qwen", "openhands", "codex", "precheck", "codex_final", "test", "browser",
    "visual_review", "completion_audit", "git_publish",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one evidence-gated Supervisor task or an entire runbook collection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  supervisor-run --task-id D005
      Run the installed runbook D005.
  supervisor-run --runbook path/to/T001.md
      Run one explicit runbook file.
  supervisor-run --task-range D007-D010
      Run a fixed sequential range; stop at the first task needing review.
  supervisor-run --run-all --runbooks-dir runbooks --initial projects/my-game/INITIAL.md
      Run a collection once through completion using the named project's brief.
      Newly created runbooks and registered child collections are discovered
      automatically.
  supervisor-run --run-initial --runbooks-dir runbooks --initial projects/my-game/INITIAL.md
      Run only the first declared task after reading the named project's brief.
  supervisor-run --project my-game
      Resume the complete factory and generated runbook collections for
      projects/my-game, using its INITIAL.md and durable per-project state.

Use supervisor initial to create INITIAL.md interactively. Use
supervisor-reports list, task-state, or show to inspect evidence and recovery
state. An accepted task is skipped on later collection runs unless --retry is
given for that task explicitly.""",
    )
    parser.add_argument("--task-id", help="Runbook task ID, for example D005. Automatically loads runbooks/<ID>.md when present.")
    parser.add_argument("--task-range", help="Sequential runbook range, for example D007-D010. Stops at the first task that is not accepted.")
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run a collection to completion, discovering runbooks and explicit child collections created during the run.",
    )
    parser.add_argument("--run-initial", action="store_true", help="Run only the first installed runbook in declared sequence order.")
    parser.add_argument(
        "--runbooks-dir",
        help="Directory containing a runbook collection; defaults to <project>/runbooks. --run-all follows its registered children.",
    )
    parser.add_argument("--initial", type=Path, help="Initial project brief for --run-all or --run-initial. Use projects/<project-name>/INITIAL.md for the runbook factory.")
    parser.add_argument("--project", help="Named workspace under projects/. Implies --run-all and uses projects/<name>/INITIAL.md plus isolated durable state.")
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
        help="Reopen a task at the primary Qwen stage while preserving its worktree and durable history; use this to re-verify an accepted task.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Re-run independent verification from Codex final review without repeating the primary implementation agent.",
    )
    parser.add_argument(
        "--start-on",
        choices=START_STAGES,
        help="Start at one named stage, skipping earlier stages and preserving normal downstream gates; useful for focused validation such as --start-on test.",
    )
    parser.add_argument(
        "--output-format",
        choices=["summary", "json"],
        default="summary",
        help="Terminal completion output. Full raw evidence remains in SQLite and live logs.",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        return
    arguments = parser.parse_args()
    if sum(bool(value) for value in (arguments.retry, arguments.verify, arguments.start_on)) > 1:
        parser.error("Choose only one of --retry, --verify, or --start-on.")
    package_root = Path(__file__).resolve().parents[1]
    project_root = load_project_environment(package_root)
    repo_root = project_path(os.getenv("SUPERVISOR_REPO_ROOT", "."), project_root)
    database_path = project_path(
        os.getenv("SUPERVISOR_DATABASE_PATH", ".state/supervisor.sqlite3"),
        repo_root,
    )
    project_workspace: Path | None = None
    if arguments.project:
        if arguments.initial:
            parser.error("--project already selects its own INITIAL.md; do not combine it with --initial.")
        try:
            project_workspace = _project_workspace(arguments.project)
        except ValueError as error:
            parser.error(str(error))
        # A generated project owns its settings and state. Load its .env after
        # the factory default so it can deliberately override generic values.
        project_root = load_project_environment(package_root, env_file=project_workspace / ".env", override=True)
        repo_root = project_path(os.getenv("SUPERVISOR_REPO_ROOT", ".."), project_root)
        database_path = project_path(os.getenv("SUPERVISOR_DATABASE_PATH", ".state/supervisor.sqlite3"), project_root)
        if not any((arguments.task_range, arguments.run_all, arguments.run_initial)):
            arguments.run_all = True
    batch_modes = sum(bool(value) for value in (arguments.task_range, arguments.run_all, arguments.run_initial))
    if batch_modes:
        if batch_modes > 1:
            parser.error("Choose only one of --task-range, --run-all, or --run-initial.")
        if any((arguments.task_id, arguments.runbook, arguments.title, arguments.objective, arguments.acceptance, arguments.start_on)):
            parser.error("Batch modes cannot be combined with a single-task runbook or ad-hoc task options.")
        if arguments.initial and not (arguments.run_all or arguments.run_initial):
            parser.error("--initial can be used only with --run-all or --run-initial.")
        if project_workspace and arguments.task_range:
            parser.error("--project runs a collection; use --run-all (the default) or --run-initial, not --task-range.")
        supplied_directory = (
            Path(arguments.runbooks_dir).expanduser()
            if arguments.runbooks_dir
            else (Path.cwd() / "runbooks" if project_workspace else repo_root / "runbooks")
        )
        runbooks_directory = supplied_directory if supplied_directory.is_absolute() else (supplied_directory.resolve() if supplied_directory.is_dir() else repo_root / supplied_directory)
        if arguments.task_range:
            try:
                task_ids = _expand_task_range(arguments.task_range)
            except ValueError as error:
                parser.error(str(error))
            runbooks = [runbooks_directory / f"{task_id}.md" for task_id in task_ids]
        else:
            try:
                runbooks = _collection_runbooks(runbooks_directory)
            except ValueError as error:
                parser.error(str(error))
            if arguments.run_initial:
                runbooks = runbooks[:1]
        missing = [str(path) for path in runbooks if not path.is_file()]
        if missing:
            parser.error("Selected collection requires an installed runbook for every task: " + ", ".join(missing))
        if not runbooks:
            parser.error(f"No runnable Markdown runbooks found in {runbooks_directory}.")
        # Generated collections intentionally reuse friendly IDs such as R0001.
        # Keep durable state beside each collection so different generated
        # projects cannot cause accepted-task or resume collisions. An explicit
        # SUPERVISOR_DATABASE_PATH remains the opt-in shared-state override.
        if project_workspace:
            database_path = project_workspace / ".state" / "factory.sqlite3"
            arguments.initial = project_workspace / "INITIAL.md"
        elif "SUPERVISOR_DATABASE_PATH" not in os.environ:
            database_path = runbooks_directory.parent / ".supervisor" / "supervisor.sqlite3"
        initial_context = ""
        if arguments.run_all or arguments.run_initial:
            try:
                initial_context = _initial_document(runbooks_directory, arguments.initial)
            except ValueError as error:
                parser.error(str(error))
        if arguments.run_all:
            completed = _run_collection_until_complete(
                runbooks_directory, arguments.dry_run, arguments.continue_on_nonpass, database_path, initial_context
            )
            if completed:
                _run_registered_collections(runbooks_directory, arguments.dry_run, arguments.continue_on_nonpass, project_workspace)
        else:
            _run_task_range(runbooks, arguments.dry_run, arguments.continue_on_nonpass, database_path, initial_context)
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
    initial_context = os.getenv("SUPERVISOR_INITIAL_CONTEXT", "").strip()
    if initial_context:
        task = task.model_copy(update={"objective": "\n\n".join((task.objective, f"Initial project brief:\n{initial_context}"))})
    store = RunStore(database_path)
    previous_state = store.state_for(task.task_id)
    # An accepted task is immutable from the supervisor's perspective.  A
    # caller can intentionally make a new task/runbook when scope changes;
    # blindly sending an already accepted task back to a slow coding model is
    # both wasteful and risky.
    if _should_skip_accepted_task(previous_state, arguments.retry or arguments.verify or arguments.start_on, repo_root):
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
    previous_qwen_session = None if arguments.retry or arguments.verify else _qwen_session_to_resume(previous_state)
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
    if not arguments.retry and not arguments.verify and _can_recover_qwen(previous_state):
        recovered_qwen = latest_qwen_result(database_path.parent / "live", task.task_id)
    if recovered_qwen and not arguments.retry and not arguments.verify:
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
    if arguments.start_on:
        progress(
            f"START-ON {arguments.start_on} · earlier stages intentionally skipped; "
            "normal downstream gates remain enabled"
        )
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
    resume_stage = arguments.start_on or ("qwen" if arguments.retry else ("codex_final" if arguments.verify or recovered_event else _resume_stage(previous_state)))
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
    if run.status is not Status.PASS:
        run.notes.append("Failure digest:\n" + summarize_failure(run))
    try:
        store.save(run)
        store.finish_task(run, _head_commit(repo_root) if run.status is Status.PASS else None)
    finally:
        store.close()
    progress(_completion_progress(run, database_path))
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


def _should_skip_accepted_task(
    state: dict | None,
    retry_requested: bool,
    repo_root: Path,
) -> bool:
    """Keep accepted tasks immutable unless the caller explicitly retries.

    ``--retry`` is an operator override for a repaired policy or an explicit
    request to repeat verification. The prior SQLite evidence is retained.
    """

    if retry_requested or not state or state.get("status") != "accepted":
        return False
    accepted_commit = state.get("accepted_commit")
    return bool(accepted_commit and _commit_exists(repo_root, str(accepted_commit)))


def _run_summary(run: TaskRun, database_path: Path) -> str:
    """Render a compact terminal handoff without duplicating raw evidence."""

    report_path = database_path.parent / "reports" / f"{run.run_id}.md"
    lines = [
        _completion_banner(run),
        f"{run.task.task_id} · {run.status.value} · {run.route}",
        f"Run: {run.run_id}",
        "Stages:",
    ]
    lines.extend(
        f"- {event.stage} ({event.agent}) · {event.status.value} → {event.route} · {event.summary}"
        for event in run.events
    )
    failure_digest = next((note for note in reversed(run.notes) if note.startswith("Failure digest:")), "")
    if failure_digest:
        lines.extend(["", failure_digest])
    lines.extend([
        f"Report: {report_path}",
        f"Evidence: {database_path}",
        f"Inspect: supervisor-reports show {run.run_id}",
        "Use --output-format json only when another program needs the full raw run payload.",
    ])
    return "\n".join(lines)


def _completion_banner(run: TaskRun) -> str:
    """Make the terminal's final outcome impossible to mistake for success."""

    if run.status is Status.PASS:
        return "SUCCESS — TASK ACCEPTED · all required supervisor stages passed."
    return (
        "NOT ACCEPTED — USER REVIEW REQUIRED · this task was not accepted, "
        "committed, or advanced as a completed task."
    )


def _completion_progress(run: TaskRun, database_path: Path) -> str:
    """Return the single-line live-log completion notice."""

    report_path = database_path.parent / "reports" / f"{run.run_id}.md"
    if run.status is Status.PASS:
        return f"SUCCESS · {run.task.task_id} accepted · report stored in SQLite and {report_path}"
    return (
        f"NOT ACCEPTED · {run.task.task_id} requires user review · "
        f"report stored in SQLite and {report_path}"
    )


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
        "prepare", "art_director", "asset_generator", "asset_finisher", "asset_qa", "qwen", "openhands", "codex", "codex_final", "precheck", "test", "browser",
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


def _collection_runbooks(directory: Path) -> list[Path]:
    """Return a collection's task contracts in their declared sequence order."""

    candidates = [path for path in directory.glob("*.md") if re.fullmatch(r"[A-Za-z]+\d+", path.stem)]
    return sorted(candidates, key=lambda path: (load_task(path).sequence, path.name))


def _initial_document(directory: Path, explicit_path: Path | None = None) -> str:
    """Load the collection context that must precede an all-task run."""

    if explicit_path is not None:
        path = explicit_path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"--initial requires an existing Markdown brief: {path}.")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"--initial requires a completed {path}.")
        return content

    # Generated collections live one level below a project workspace and inherit
    # its normalised brief. The source factory receives its brief explicitly by
    # --initial, preventing a global CLI from accidentally reading another
    # project's INITIAL.md.
    candidates = (directory / "INITIAL.md", directory.parent / "PROJECT_BRIEF.md")
    for path in candidates:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
            raise ValueError(f"--run-all requires a completed {path}.")
    expected = " or ".join(str(path) for path in candidates)
    raise ValueError(f"--run-all requires {expected}.")


def _project_workspace(value: str) -> Path:
    """Resolve a named project workspace without consulting the CLI install path."""

    requested = Path(value).expanduser()
    if requested.is_absolute():
        workspace = requested
    elif requested.parts and requested.parts[0] == "projects":
        workspace = Path.cwd() / requested
    else:
        workspace = Path.cwd() / "projects" / requested
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ValueError(f"--project requires an existing workspace: {workspace}. Run `supervisor initial` first.")
    initial = workspace / "INITIAL.md"
    if not initial.is_file():
        raise ValueError(f"--project requires {initial}. Run `supervisor initial` first.")
    return workspace


def _run_task_range(
    runbooks: list[Path], dry_run: bool, continue_on_nonpass: bool, database_path: Path, initial_context: str = ""
) -> bool:
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
        environment = os.environ.copy()
        environment["SUPERVISOR_DATABASE_PATH"] = str(database_path)
        if initial_context:
            environment["SUPERVISOR_INITIAL_CONTEXT"] = initial_context
        completed = subprocess.run(command, check=False, env=environment)
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
            return False
        print(message + " · continuing by explicit override", file=sys.stderr, flush=True)
    print("BATCH FINAL · all requested tasks were started", file=sys.stderr, flush=True)
    return True


def _run_collection_until_complete(
    directory: Path, dry_run: bool, continue_on_nonpass: bool, database_path: Path, initial_context: str
) -> bool:
    """Run a collection until no unaccepted task remains.

    A task may create more runbooks. Re-enumerating only after an accepted pass
    lets a single invocation consume a bounded, dynamically growing collection
    without precomputing an unbounded task list.
    """

    wave = 0
    while True:
        runbooks = _collection_runbooks(directory)
        if not runbooks:
            raise ValueError(f"No runnable Markdown runbooks found in {directory}.")
        store = RunStore(database_path)
        try:
            pending = [path for path in runbooks if (store.state_for(path.stem) or {}).get("status") != "accepted"]
        finally:
            store.close()
        if not pending:
            print(f"COLLECTION FINAL · {directory} has no pending tasks", file=sys.stderr, flush=True)
            return True
        wave += 1
        print(f"COLLECTION WAVE {wave:02d} · {len(pending)} pending tasks in {directory}", file=sys.stderr, flush=True)
        if not _run_task_range(pending, dry_run, continue_on_nonpass, database_path, initial_context):
            return False


def _run_registered_collections(
    directory: Path, dry_run: bool, continue_on_nonpass: bool, project_workspace: Path | None = None
) -> None:
    """Follow explicit child-collection registrations after a parent completes."""

    registrations = directory / ".supervisor-children"
    if not registrations.is_dir():
        return
    for registration in sorted(registrations.glob("*.json")):
        try:
            payload = json.loads(registration.read_text(encoding="utf-8"))
            child_value = payload["runbooks_dir"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"Invalid child collection registration {registration}: {error}") from error
        if not isinstance(child_value, str) or not child_value.strip():
            raise ValueError(f"Invalid child collection registration {registration}: runbooks_dir must be a path string.")
        child_directory = (directory / child_value).resolve()
        if not child_directory.is_dir():
            raise ValueError(f"Registered child collection does not exist: {child_directory} ({registration})")
        if project_workspace and not child_directory.is_relative_to(project_workspace):
            continue
        if project_workspace:
            child_database = project_workspace / ".state" / f"{child_directory.name}.sqlite3"
        else:
            child_database = child_directory.parent / ".state" / "supervisor.sqlite3"
        child_context = _initial_document(child_directory)
        if _run_collection_until_complete(child_directory, dry_run, continue_on_nonpass, child_database, child_context):
            _run_registered_collections(child_directory, dry_run, continue_on_nonpass, project_workspace)


if __name__ == "__main__":
    main()
