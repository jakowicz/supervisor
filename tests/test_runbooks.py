from pathlib import Path

import pytest

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
    path = tmp_path / "R0002.md"
    path.write_text(
        "---\ntask_id: R0002\nsequence: 1\ntitle: Gate\nbrowser_impact: not_applicable\nplaywright_spec: \n"
            "asset_impact: required\nasset_brief: docs/art/briefs/gate.md\nasset_ids: gate,gate_build\nvisual_style_version: project-v1\naudio_impact: not_applicable\naudio_ids: \naudio_brief: \naudio_duration_seconds: 0\naudio_loop: not_applicable\naudio_style_version: \nsource_specifications: specification/04-experience-contract.md#gate\nsource_catalogue_ids: IMP-WORLD-001\nauthoring_batch: B0001\nfactory_stages: F004,F012,F013\n---\n"
        "# R0002\n\n## Objective\n\nMake a gate.\n\n## Acceptance criteria\n\n- It is original.\n",
        encoding="utf-8",
    )
    task = load_task(path)
    assert task.asset_impact == "required"
    assert task.asset_ids == ["gate", "gate_build"]
    assert task.authoring_batch == "B0001"
    assert task.source_catalogue_ids == ["IMP-WORLD-001"]
    assert task.visual_style_version == "project-v1"


def test_r_series_runbook_must_explicitly_assess_assets(tmp_path):
    path = tmp_path / "R0001.md"
    path.write_text(
        "---\ntask_id: R0001\nsequence: 1\ntitle: Missing asset assessment\nbrowser_impact: not_applicable\nplaywright_spec:\nsource_specifications: specification/02-feature-model.md#loop\nsource_catalogue_ids: IMP-LOOP-001\nauthoring_batch: B0001\nfactory_stages: F002,F012,F013\n---\n"
        "## Objective\n\nDo the work.\n\n## Acceptance criteria\n\n- The work is done.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must declare asset metadata"):
        load_task(path)


def test_r_series_runbook_must_declare_provenance(tmp_path):
    path = tmp_path / "R0001.md"
    path.write_text(
        "---\ntask_id: R0001\nsequence: 1\ntitle: Missing provenance\nbrowser_impact: not_applicable\nplaywright_spec:\nasset_impact: not_applicable\nasset_ids:\n---\n"
        "## Objective\n\nDo the work.\n\n## Acceptance criteria\n\n- The work is done.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must declare provenance metadata"):
        load_task(path)


def test_r_series_asset_task_requires_stable_asset_ids(tmp_path):
    path = tmp_path / "R0001.md"
    path.write_text(
        "---\ntask_id: R0001\nsequence: 1\ntitle: Asset work\nbrowser_impact: not_applicable\nplaywright_spec:\nasset_impact: required\nasset_ids:\nsource_specifications: specification/04-experience-contract.md#gate\nsource_catalogue_ids: IMP-WORLD-001\nauthoring_batch: B0001\nfactory_stages: F004,F012,F013\n---\n"
        "## Objective\n\nDo the work.\n\n## Acceptance criteria\n\n- The work is done.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="has no asset_ids"):
        load_task(path)


def test_r_series_dependencies_are_loaded_as_execution_gates(tmp_path):
    path = tmp_path / "R0002.md"
    path.write_text(
        "---\ntask_id: R0002\nsequence: 2\ntitle: Dependency task\nbrowser_impact: not_applicable\nplaywright_spec:\ndependencies: R0001,R0003\nsource_specifications: specification/02-feature-model.md#loop\nsource_catalogue_ids: IMP-LOOP-002\nauthoring_batch: B0001\nfactory_stages: F002,F012,F013\nasset_impact: not_applicable\nasset_ids:\naudio_impact: not_applicable\naudio_ids:\naudio_brief:\naudio_duration_seconds: 0\naudio_loop: not_applicable\naudio_style_version:\n---\n\n## Objective\n\nDo the work.\n\n## Acceptance criteria\n\n- The work is done.\n",
        encoding="utf-8",
    )

    assert load_task(path).dependencies == ["R0001", "R0003"]


def test_r_series_dependencies_reject_invalid_ids(tmp_path):
    path = tmp_path / "R0002.md"
    path.write_text(
        "---\ntask_id: R0002\nsequence: 2\ntitle: Invalid dependency\nbrowser_impact: not_applicable\nplaywright_spec:\ndependencies: R0001,not-a-task\nsource_specifications: specification/02-feature-model.md#loop\nsource_catalogue_ids: IMP-LOOP-002\nauthoring_batch: B0001\nfactory_stages: F002,F012,F013\nasset_impact: not_applicable\nasset_ids:\naudio_impact: not_applicable\naudio_ids:\naudio_brief:\naudio_duration_seconds: 0\naudio_loop: not_applicable\naudio_style_version:\n---\n\n## Objective\n\nDo the work.\n\n## Acceptance criteria\n\n- The work is done.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid dependencies"):
        load_task(path)
