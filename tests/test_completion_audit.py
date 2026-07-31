from supervisor.completion_audit import audit
from supervisor.models import AcceptanceResult, CriterionStatus, DocumentationReport, NextStep, RunEvent, Status, Task, WorkerResult


def coding_event(result: WorkerResult) -> RunEvent:
    return RunEvent(stage="qwen", agent="Qwen", model="model", attempt=1, status=result.status, summary=result.summary, route="test", result=result)


def test_audit_rejects_missing_criterion_and_docs_review():
    task = Task(task_id="T01", title="Test", acceptance_criteria=["A"])
    result = WorkerResult(status=Status.PASS, summary="done", test_result="flutter test passed", recommended_next_step=NextStep.COMPLETE)
    assert audit(task, [coding_event(result)]).status is Status.REPAIRABLE_FAILURE


def test_audit_accepts_complete_report():
    task = Task(task_id="T01", title="Test", acceptance_criteria=["A"])
    result = WorkerResult(
        status=Status.PASS, summary="done", test_result="flutter test passed",
        acceptance_results=[AcceptanceResult(criterion="A", status=CriterionStatus.PASS, evidence="test A")],
        documentation=DocumentationReport(reviewed_files=["README.md"], summary="No change needed."),
        recommended_next_step=NextStep.COMPLETE,
    )
    assert audit(task, [coding_event(result)]).status is Status.PASS
