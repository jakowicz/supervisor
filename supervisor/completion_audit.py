"""Deterministic completion-contract audit before a task can be accepted."""

from __future__ import annotations

from .models import NextStep, Status, Task, WorkerResult


CODING_STAGES = {"qwen", "openhands", "codex", "codex_final"}


def audit(task: Task, events: list) -> WorkerResult:
    coding_events = [event for event in events if event.stage in CODING_STAGES]
    if not coding_events:
        return WorkerResult(status=Status.REPAIRABLE_FAILURE, summary="No coding-agent completion report exists.", recommended_next_step=NextStep.RETRY_QWEN)
    result = coding_events[-1].result
    reported = {item.criterion: item for item in result.acceptance_results}
    exact_results = [reported.get(criterion) for criterion in task.acceptance_criteria]
    if all(exact_results):
        matched_results = exact_results
    elif len(result.acceptance_results) == len(task.acceptance_criteria):
        # Local models frequently preserve the required order but restate a
        # long Markdown bullet. The prompt requires one result per criterion;
        # accept that ordered contract while retaining the original evidence.
        matched_results = result.acceptance_results
    else:
        matched_results = exact_results
    missing = [
        criterion
        for criterion, item in zip(task.acceptance_criteria, matched_results)
        if item is None
    ]
    unverified = [item.criterion for item in matched_results if item is not None and item.status.value != "pass"]
    docs_reviewed = result.documentation.reviewed_files
    problems: list[str] = []
    if missing:
        problems.append("missing acceptance evidence for: " + "; ".join(missing))
    if unverified:
        problems.append("criteria not passed: " + "; ".join(unverified))
    if not docs_reviewed:
        problems.append("no README/documentation review was reported")
    if not result.test_result:
        problems.append("no coding-agent test summary was reported")
    if task.browser_impact == "required" and not result.browser_coverage:
        problems.append("browser-impacting task has no Playwright coverage summary")
    if problems:
        return WorkerResult(
            status=Status.REPAIRABLE_FAILURE,
            summary="Completion contract is incomplete: " + "; ".join(problems),
            test_result=result.test_result,
            recommended_next_step=NextStep.RETRY_QWEN,
        )
    return WorkerResult(
        status=Status.PASS,
        summary="Completion contract covers every acceptance criterion, documentation review, and coding-agent checks.",
        test_result=result.test_result,
        acceptance_results=result.acceptance_results,
        documentation=result.documentation,
        known_limitations=result.known_limitations,
        recommended_next_step=NextStep.COMPLETE,
    )
