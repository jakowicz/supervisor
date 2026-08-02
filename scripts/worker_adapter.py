"""Shared safeguards and JSON conversion for local coding-agent adapters."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from supervisor.result_parser import parse_worker_result


def repository_root() -> Path:
    configured = os.getenv("SUPERVISOR_REPO_ROOT")
    if not configured:
        raise RuntimeError("SUPERVISOR_REPO_ROOT is not configured.")
    return Path(configured).resolve()


def writes_are_enabled() -> bool:
    return os.getenv("SUPERVISOR_ALLOW_AUTONOMOUS_WRITES") == "true"


def worker_timeout_seconds() -> int:
    return int(os.getenv("SUPERVISOR_WORKER_TIMEOUT_SECONDS", "1800"))


def safety_gate(worker_name: str, next_step: NextStep) -> WorkerResult | None:
    if writes_are_enabled():
        return None
    return WorkerResult(
        status=Status.NEEDS_USER_REVIEW,
        summary=(
            f"{worker_name} is configured but autonomous writes are disabled. "
            "Set SUPERVISOR_ALLOW_AUTONOMOUS_WRITES=true only after reviewing its sandbox and credentials."
        ),
        recommended_next_step=next_step,
    )


def _copy_stream(stream, captured: list[str], label: str, last_output: list[float]) -> None:
    """Capture process output while optionally teeing it into the live stage log."""

    stream_path = os.getenv("SUPERVISOR_STREAM_LOG")
    for line in iter(stream.readline, ""):
        captured.append(line)
        last_output[0] = time.monotonic()
        if stream_path:
            with Path(stream_path).open("a", encoding="utf-8") as output:
                output.write(f"[{label}] {line}")
    stream.close()


def _descendant_pids(root_pid: int) -> list[int]:
    """Return descendants deepest-first; Qwen's sandbox makes new groups."""

    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=False
        ).stdout.splitlines()
    except OSError:
        return []
    children: dict[int, list[int]] = {}
    for line in listing:
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            continue
        pid, parent = map(int, fields)
        children.setdefault(parent, []).append(pid)

    descendants: list[int] = []

    def visit(parent: int) -> None:
        for child in children.get(parent, []):
            visit(child)
            descendants.append(child)

    visit(root_pid)
    return descendants


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate a CLI and its sandbox descendants, even across groups."""

    pids = _descendant_pids(process.pid)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def run_command(
    command: list[str], repo_root: Path, idle_timeout_seconds: int | None = None
) -> tuple[int, str, str]:
    """Run a local agent, failing early when it produces no progress output."""

    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout: list[str] = []
        stderr: list[str] = []
        last_output = [time.monotonic()]
        stdout_thread = threading.Thread(
            target=_copy_stream, args=(process.stdout, stdout, "stdout", last_output), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_copy_stream, args=(process.stderr, stderr, "stderr", last_output), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            deadline = time.monotonic() + worker_timeout_seconds()
            while True:
                try:
                    return_code = process.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
                    if idle_timeout_seconds and time.monotonic() - last_output[0] >= idle_timeout_seconds:
                        _stop_process_group(process)
                        stdout_thread.join()
                        stderr_thread.join()
                        return (
                            1,
                            "".join(stdout),
                            f"Worker made no stdout/stderr progress for {idle_timeout_seconds} seconds; terminated safely.",
                        )
        except subprocess.TimeoutExpired:
            _stop_process_group(process)
            stdout_thread.join()
            stderr_thread.join()
            return 1, "".join(stdout), f"Worker timed out after {worker_timeout_seconds()} seconds; terminated safely."
        stdout_thread.join()
        stderr_thread.join()
        return return_code, "".join(stdout), "".join(stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "", str(error)


def failure(worker_name: str, summary: str, next_step: NextStep, stdout: str = "", stderr: str = "") -> WorkerResult:
    return WorkerResult(
        status=Status.ENVIRONMENT_FAILURE,
        summary=f"{worker_name}: {summary}",
        evidence=Evidence(test_log=stdout + stderr),
        recommended_next_step=next_step,
    )


def parse_openhands_result(output: str) -> WorkerResult | None:
    """Read a strict result or normalise OpenHands' Finish-action message.

    OpenHands emits its final answer inside a JSONL ``FinishAction`` event.
    Local models sometimes omit the supervisor's convenience fields even after
    being asked for the full contract. A normalised result is still sent to
    independent precheck/final-review gates, rather than losing completed work
    merely because its transport wrapper differs from Qwen/Codex.
    """

    strict = parse_worker_result(output)
    if strict:
        return strict

    # The OpenHands CLI mixes human-readable startup/progress lines with JSONL
    # events.  The FinishAction can therefore be perfectly valid while the
    # complete stdout string is not itself JSON.  Inspect each JSONL record as
    # well as the complete response before walking nested event payloads.
    pending: list[object] = [output, *(
        line for line in output.splitlines() if line.lstrip().startswith(("{", "["))
    )]
    while pending:
        value = pending.pop(0)
        if isinstance(value, str):
            try:
                pending.append(json.loads(value))
            except json.JSONDecodeError:
                continue
            continue
        if isinstance(value, list):
            pending.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        pending.extend(value.values())
        status_value = value.get("status")
        if status_value not in {status.value for status in Status}:
            continue
        status = Status(status_value)
        raw_documentation = value.get("documentation")
        documentation_summary = (
            json.dumps(raw_documentation, sort_keys=True)
            if isinstance(raw_documentation, dict)
            else "OpenHands final response did not include a documentation summary."
        )
        return WorkerResult(
            status=status,
            summary=str(value.get("summary") or "OpenHands completed its Finish action."),
            changed_files=value.get("changed_files") if isinstance(value.get("changed_files"), list) else [],
            test_result=(
                value.get("test_result")
                if isinstance(value.get("test_result"), str)
                else json.dumps(value.get("test_result", ""), sort_keys=True)
            ),
            acceptance_results=(
                value.get("acceptance_results") if isinstance(value.get("acceptance_results"), list) else []
            ),
            documentation={"summary": documentation_summary},
            known_limitations=(
                value.get("known_limitations") if isinstance(value.get("known_limitations"), list) else []
            ),
            browser_coverage=str(value.get("browser_coverage") or ""),
            recommended_next_step=(
                value.get("recommended_next_step")
                or (NextStep.COMPLETE.value if status is Status.PASS else NextStep.USE_CODEX.value)
            ),
        )
    return None


def task_prompt(
    task: Task,
    *,
    completion_mode: str = "structured_output",
    codex_sandbox: bool = False,
) -> str:
    task_json = json.dumps(model_to_dict(task), indent=2)
    static_browser_instructions = ""
    if os.getenv("SUPERVISOR_BROWSER_QA_MODE") == "static":
        static_browser_instructions = """

This repository uses a deliberately dependency-free static browser QA worker.
Do not install or attempt to launch Playwright, Chromium, a development server,
or another real browser. For UI acceptance criteria, verify the relevant
HTML/module/event wiring by inspection and focused unit tests. The configured
`browser` stage after this coding stage is the authoritative browser-contract
check. Do not return needs_user_review merely because a real browser executable
is unavailable.
"""
    final_verification_instructions = ""
    if task.execution_mode == "final_verification":
        final_verification_instructions = """

You are the mandatory final verifier/fixer after a successful Qwen or
OpenHands implementation. Treat the current worktree as the candidate
solution: first inspect the runbook acceptance criteria and the actual changes,
then run the most relevant checks. Do not reimplement correct work or broaden
scope. If a criterion is incomplete, a test is missing, documentation is stale,
or a check exposes a defect, fix it in this pass and rerun the affected checks.
The deterministic `test` stage which follows this review is authoritative for
Flutter analysis, Flutter tests, builds, and project verification scripts. Do
not run `flutter`, `dart`, or an SDK/bootstrap command from this Codex sandbox:
those tools may legitimately write SDK caches outside the worktree and require
an unavailable interactive approval. Do not report a repairable failure merely
because such a command cannot run here. Instead, review the implementation and
its focused source/test evidence, repair any clear gap, then return `pass` to
hand the candidate to the independent test stage. Return `repairable_failure`
only when a concrete implementation or acceptance gap remains that you cannot
finish; the supervisor will retry this same Codex final-review stage up to its
configured limit.

The repository may be a shared, already-dirty worktree with unrelated earlier
tasks. Do not return `needs_user_review` merely because unrelated changes are
present or this task is not committed yet. Review the task-scoped files and
evidence; the deterministic Git publisher later enforces the accepted commit
scope. Return `needs_user_review` only for a real product/safety decision that
cannot be resolved from the runbook and repository evidence.
"""
    codex_sandbox_instructions = ""
    if codex_sandbox:
        codex_sandbox_instructions = """

This Codex worker runs in a workspace-only sandbox. Do not invoke `flutter`,
`dart`, SDK/bootstrap tooling, or request an approval escalation: those tools
can write SDK caches outside the worktree and are deliberately owned by the
supervisor's independent precheck/test stages. When the continuation evidence
names a concrete source or test failure, repair that evidence in the worktree,
run only safe focused checks, and return `pass` for the deterministic stage to
validate. Never return a failure solely because an SDK command needs approval.
"""
    if completion_mode == "finish_action":
        completion_protocol = """Completion protocol:
- OpenHands has no `structured_output` tool. Your final action must be its
  Finish action, and the Finish action's message must be exactly one valid
  WorkerResult JSON object: no prose, Markdown fences, or explanation before
  or after it.
- Use native nested JSON values: `evidence` and `documentation` are objects,
  while `acceptance_results` is an array. Include every required field.
- If a check cannot run, return a `repairable_failure` or
  `needs_user_review` object immediately with the exact reason. Never wait
  silently for a tool or model request to recover.
"""
    else:
        completion_protocol = """Completion protocol:
- Your final action MUST be the `structured_output` tool supplied by
  `--json-schema`. Do not end with prose, Markdown, or a hand-written JSON
  response.
- Use native nested JSON values in that tool call: `evidence` and
  `documentation` are objects, while `acceptance_results` is an array. Never
  put JSON text inside quotes for any of those fields.
- If a check cannot run, return a structured `repairable_failure` or
  `needs_user_review` result immediately with the exact reason. Never wait
  silently for a tool or model request to recover.
"""
    return f"""You are the coding worker for one isolated runbook task.

Work only in the repository passed as your working directory. Do not publish,
deploy, access secrets, make payments, or change product scope. Stop and report
needs_user_review if the task is destructive, unclear, or requires authority.
Implement only the task below, run relevant checks, and then output exactly one
JSON object conforming to the WorkerResult contract. A status of pass means the
change is ready for independent test and visual review; it is not self-approval.

Task:
{task_json}

Continuation checkpoint (only when present):
{task.continuation_context or "No prior checkpoint: inspect the current repository before making changes."}
{final_verification_instructions}
{codex_sandbox_instructions}
{static_browser_instructions}

Efficient investigation rules (especially important for local models):
- Start with the Codebase Memory MCP for architecture and targeted symbols.
- Do not use whole-file reads for Dart, test, lock, generated, or log files.
  Use targeted symbol snippets or `run_shell_command` with `rg` and
  `sed -n 'START,ENDp'`; keep each inspected excerpt to 200 lines or fewer.
- Inspect only files directly relevant to the acceptance criteria. Do not
  browse unrelated Playwright, automation, or configuration files merely to
  learn the repository layout.
- After the focused investigation, implement and run the smallest relevant
  checks. Do not continue exploratory tool calls once the implementation path
  is clear.
- If a tool reports a missing file, treat it as non-blocking and continue with
  the repository evidence you already have.

{completion_protocol}

Your final WorkerResult must include all of the following:
- `acceptance_results`: one entry for every task acceptance criterion, in the
  same order. Copy each criterion text verbatim from the task, with pass/fail/
  not_verified status and concrete evidence;
- `documentation`: README/plan/ADR/runbook files reviewed, files updated, and
  why they were or were not changed;
- `test_result`: exact checks you ran and their outcomes;
- `known_limitations`: remaining gaps or an empty list.

Do not claim `pass` if a criterion is unverified. Update user-facing README or
developer documentation whenever the change affects behaviour, setup, commands,
architecture, design, or progress. If no documentation change is needed, state
the reviewed files and explain why.

For `final_verification`, the independent `test`, `browser`, and visual-review
stages are the required verification gates. After repairing every concrete
source/test failure supplied in this prompt, return `pass` so those gates run.
Do not return `needs_user_review` solely because Flutter/Dart was deliberately
left to the independent test stage. Return `needs_user_review` only for a real
authority, safety, or irreducibly ambiguous product decision.

For browser-impacting work, create or update a task-specific Playwright spec in
`supervisor/browser/tests/changes/`, list it in `browser_coverage`,
and revise stale checks. Keep `tests/smoke/` short and stable. For domain-only
work, explain in `browser_coverage` why no task-specific browser spec changed.

Visual baseline rule:
- When the continuation evidence names a golden, snapshot, or visual-regression
  failure, inspect the supplied master/test/diff artifacts before changing any
  baseline. Work out whether the new render is the intended result of this
  task, and check that it has no clipping, overlap, or lost meaning.
- If it is intentional, regenerate only the affected approved baselines using
  the repository's documented command, update stale visual/browser checks when
  their prior expectation is no longer correct, and rerun the relevant suite.
- If it is not intentional, fix the product or test setup instead. Never mask
  a real visual regression by blindly accepting a changed screenshot.
"""


def emit(result: WorkerResult) -> None:
    print(json.dumps(model_to_dict(result)))
