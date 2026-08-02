from supervisor.models import Status, Task
from supervisor.workers import browser, visual_review


def test_document_only_runbooks_bypass_browser_and_visual_workers(monkeypatch):
    task = Task(task_id="F001", title="Document factory", browser_impact="not_applicable")
    monkeypatch.delenv("BROWSER_QA_COMMAND", raising=False)
    monkeypatch.delenv("VISUAL_REVIEW_COMMAND", raising=False)

    browser_result = browser.run(task)
    visual_result = visual_review.run(task)

    assert browser_result.status is Status.PASS
    assert "not applicable" in browser_result.summary
    assert visual_result.status is Status.PASS
    assert "not applicable" in visual_result.summary
