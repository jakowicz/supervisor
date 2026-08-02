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


def test_wrapped_acceptance_bullets_are_loaded_in_full():
    root = Path(__file__).resolve().parents[2]
    task = load_task(root / "runbooks" / "D011.md")

    assert task.acceptance_criteria[0].endswith(
        "raw presentation colour literals are introduced outside palette/theme code."
    )
    assert task.acceptance_criteria[-1] == (
        "Only this semantic-colour slice, its tests, and its browser evidence are committed."
    )


def test_runbook_accepts_opt_in_asset_metadata(tmp_path):
    path = tmp_path / "A001.md"
    path.write_text(
        "---\ntask_id: A001\nsequence: 1\ntitle: Gate\nbrowser_impact: not_applicable\nplaywright_spec: \n"
        "asset_impact: required\nasset_brief: docs/art/briefs/gate.md\nasset_ids: gate,gate_build\nvisual_style_version: emberhold-v1\n---\n"
        "# A001\n\n## Objective\n\nMake a gate.\n\n## Acceptance criteria\n\n- It is original.\n",
        encoding="utf-8",
    )
    task = load_task(path)
    assert task.asset_impact == "required"
    assert task.asset_ids == ["gate", "gate_build"]
    assert task.visual_style_version == "emberhold-v1"
