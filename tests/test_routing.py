from supervisor.models import NextStep, Status, WorkerResult
from supervisor.routing import agent_limits, next_agent, next_route


def result(status: Status) -> WorkerResult:
    return WorkerResult(status=status, summary="test", recommended_next_step=NextStep.COMPLETE)


def test_primary_or_openhands_pass_requires_codex_final_review_then_qa():
    assert next_route("qwen", result(Status.PASS), "qwen", {"qwen": 1}) == "codex_final"
    assert next_route("openhands", result(Status.PASS), "openhands", {"openhands": 1}) == "codex_final"
    assert next_route("codex_final", result(Status.PASS), "codex_final", {"codex_final": 1}) == "test"
    assert next_route("test", result(Status.PASS), "qwen", {"qwen": 1}) == "browser"
    assert next_route("browser", result(Status.PASS), "qwen", {"qwen": 1}) == "visual_review"
    assert next_route("visual_review", result(Status.PASS), "qwen", {"qwen": 1}) == "completion_audit"
    assert next_route("completion_audit", result(Status.PASS), "qwen", {"qwen": 1}) == "git_publish"
    assert next_route("git_publish", result(Status.PASS), "qwen", {"qwen": 1}) == "accept"


def test_qwen_gets_one_attempt_then_openhands_then_codex():
    assert next_route("qwen", result(Status.REPAIRABLE_FAILURE), "qwen", {"qwen": 1}) == "openhands"
    assert next_route("openhands", result(Status.REPAIRABLE_FAILURE), "openhands", {"openhands": 1}) == "codex"
    assert next_route("codex", result(Status.REPAIRABLE_FAILURE), "codex", {"codex": 1}) == "codex"
    assert next_route("codex", result(Status.REPAIRABLE_FAILURE), "codex", {"codex": 3}) == "user_review"


def test_codex_fallback_success_does_not_trigger_a_redundant_final_pass():
    assert next_route("codex", result(Status.PASS), "codex", {"codex": 1}) == "test"


def test_final_codex_review_retries_up_to_its_own_budget():
    assert next_route("codex_final", result(Status.REPAIRABLE_FAILURE), "codex_final", {"codex_final": 1}) == "codex_final"
    assert next_route("codex_final", result(Status.REPAIRABLE_FAILURE), "codex_final", {"codex_final": 3}) == "user_review"


def test_each_fresh_task_starts_with_a_full_qwen_budget():
    assert next_agent("qwen", {}) == "qwen"
    assert next_agent("qwen", {"qwen": 1}) == "openhands"


def test_retry_budget_can_be_configured_per_project(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_QWEN_ATTEMPTS", "2")
    monkeypatch.setenv("SUPERVISOR_CODEX_ATTEMPTS", "4")

    assert agent_limits()["qwen"] == 2
    assert next_agent("qwen", {"qwen": 1}) == "qwen"
    assert next_agent("qwen", {"qwen": 2}) == "openhands"
    assert agent_limits()["codex"] == 4


def test_environment_escalation_moves_through_configured_fallbacks():
    assert next_route("qwen", result(Status.ENVIRONMENT_FAILURE), "qwen", {"qwen": 1}) == "openhands"
    assert next_route("openhands", result(Status.ENVIRONMENT_FAILURE), "openhands", {"openhands": 1}) == "codex"
    assert next_route("browser", result(Status.ENVIRONMENT_FAILURE), "qwen", {"qwen": 1}) == "user_review"
