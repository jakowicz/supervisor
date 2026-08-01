from supervisor.models import NextStep, Status, WorkerResult
from supervisor.routing import first_stage, implementation_agents, next_route, primary_agent


def test_project_can_select_codex_as_its_only_coding_agent(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CODING_AGENTS", "codex")
    monkeypatch.setenv("SUPERVISOR_AGENT_ORDER", "codex")

    assert implementation_agents() == ("codex",)
    assert primary_agent() == "codex"
    assert first_stage() == "codex"
    result = WorkerResult(status=Status.PASS, summary="done", recommended_next_step=NextStep.COMPLETE)
    assert next_route("codex", result, "codex", {"codex": 1}) == "test"


def test_stale_partial_stage_order_cannot_bypass_required_qa(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CODING_AGENTS", "qwen,openhands,codex")
    monkeypatch.setenv(
        "SUPERVISOR_AGENT_ORDER",
        "codex,test,browser,visual_review,completion_audit,git_publish",
    )
    result = WorkerResult(status=Status.PASS, summary="done", recommended_next_step=NextStep.COMPLETE)

    assert first_stage() == "qwen"
    assert next_route("qwen", result, "qwen", {"qwen": 1}) == "precheck"
