from pathlib import Path

from supervisor.runbooks import load_task


def test_d005_runbook_loads_the_persistence_contract():
    root = Path(__file__).resolve().parents[2]
    task = load_task(root / "runbooks" / "D005.md")
    assert task.task_id == "D005"
    assert task.sequence == 5
    assert "persistence" in task.objective.lower()
    assert task.playwright_specs == ["tests/changes/d005-settings-restore.spec.cjs"]
    assert len(task.acceptance_criteria) == 8


def test_every_installed_runbook_loads_and_matches_its_filename():
    root = Path(__file__).resolve().parents[2]
    for path in (root / "runbooks").glob("D*.md"):
        task = load_task(path)
        assert task.task_id == path.stem
        if task.browser_impact == "required":
            assert task.playwright_specs
        else:
            assert not task.playwright_specs
