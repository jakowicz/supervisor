from supervisor.graph import _preserve_repair_loop, _repair_handoff
from supervisor.models import Evidence, NextStep, Status, WorkerResult


def test_browser_repair_handoff_prefers_raw_browser_failure_log():
    result = WorkerResult(
        status=Status.REPAIRABLE_FAILURE,
        summary="Playwright browser QA failed.",
        test_result="Playwright returned a failing exit code.",
        evidence=Evidence(browser_log="locator.click: Run diagnostics was not found"),
        recommended_next_step=NextStep.RETRY_QWEN,
    )

    handoff = _repair_handoff("browser", result)

    assert "locator.click" in handoff
    assert "not an environment failure" in handoff
    assert "Playwright returned a failing exit code" not in handoff


def test_non_browser_repair_handoff_uses_test_evidence():
    result = WorkerResult(
        status=Status.REPAIRABLE_FAILURE,
        summary="Flutter checks failed.",
        test_result="flutter test: expected true, got false",
        recommended_next_step=NextStep.RETRY_QWEN,
    )

    assert _repair_handoff("test", result) == "flutter test: expected true, got false"


def test_review_response_during_terminal_repair_stays_in_the_bounded_repair_loop():
    result = WorkerResult(
        status=Status.NEEDS_USER_REVIEW,
        summary="Manifest should stay pending.",
        recommended_next_step=NextStep.ASK_USER,
    )

    repaired = _preserve_repair_loop(result, repairing_terminal_failure=True)

    assert repaired.status is Status.REPAIRABLE_FAILURE
    assert repaired.recommended_next_step is NextStep.RETRY_QWEN
    assert "bounded automatic repair loop" in repaired.summary


def test_review_response_without_a_failed_terminal_stage_remains_a_user_decision():
    result = WorkerResult(
        status=Status.NEEDS_USER_REVIEW,
        summary="Choose a product direction.",
        recommended_next_step=NextStep.ASK_USER,
    )

    assert _preserve_repair_loop(result, repairing_terminal_failure=False) is result
