import sys

import pytest

from pathlib import Path

from supervisor import cli
from supervisor.cli import START_STAGES, _authoring_output_errors, _can_recover_qwen, _collection_runbooks, _completion_banner, _expand_task_range, _initial_document, _project_database_for_runbook, _project_workspace, _qwen_session_to_resume, _recovered_qwen_event, _reopen_invalid_authoring_tasks, _resume_stage, _retry_stage, _run_collection_until_complete, _run_registered_collections, _run_summary, _should_skip_accepted_task, _unmet_dependencies
from supervisor.failure_summary import _deterministic_summary, summarize_failure
from supervisor.models import NextStep, RunEvent, Status, Task, TaskRun, WorkerResult
from supervisor.storage import RunStore


def test_resume_starts_at_the_saved_next_stage():
    assert _resume_stage({"status": "interrupted", "next_action": "test"}) == "test"
    assert _resume_stage({"status": "interrupted", "next_action": "codex_final"}) == "codex_final"
    assert _resume_stage({"status": "validating", "next_action": "browser"}) == "browser"
    assert _resume_stage({"status": "accepted", "next_action": "qwen"}) == "prepare"
    assert _resume_stage({"status": "interrupted", "next_action": "unexpected"}) == "prepare"
    assert _resume_stage({"status": "validating", "next_action": "asset_qa"}) == "asset_qa"
    assert "test" in START_STAGES
    assert "browser" in START_STAGES


def test_collection_stops_before_relaunching_a_task_that_needs_user_review(tmp_path, capsys):
    factory = tmp_path / "runbooks"
    factory.mkdir()
    (factory / "F001.md").write_text(
        "---\ntask_id: F001\nsequence: 1\ntitle: Blocked\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nBlocked.\n\n## Acceptance criteria\n\n- Blocked.\n",
        encoding="utf-8",
    )
    database = tmp_path / ".state" / "supervisor.sqlite3"
    store = RunStore(database)
    store.claim_task("F001", "run-1", 999999)
    store.finish_task(TaskRun(task=Task(task_id="F001", title="Blocked"), run_id="run-1", status=Status.NEEDS_USER_REVIEW, route="needs_user_review", worker_results=[]))
    store.close()

    assert _run_collection_until_complete(factory, False, False, database, "# Brief") is False
    assert "COLLECTION BLOCKED · F001 requires user review" in capsys.readouterr().err


def test_resume_restores_a_qwen_session_for_an_unfinished_task():
    assert _qwen_session_to_resume({"status": "interrupted", "agent_session_id": "qwen-session"}) == "qwen-session"
    assert _qwen_session_to_resume({"status": "accepted", "agent_session_id": "qwen-session"}) is None
    assert _qwen_session_to_resume({"status": "interrupted"}) is None


def test_retry_reopens_an_accepted_task_for_verification(monkeypatch, tmp_path):
    state = {"status": "accepted", "accepted_commit": "abc123"}
    monkeypatch.setattr("supervisor.cli._commit_exists", lambda *_args: True)

    assert _should_skip_accepted_task(state, False, tmp_path) is True
    assert _should_skip_accepted_task(state, True, tmp_path) is False


def test_retry_uses_the_project_configured_primary_agent(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CODING_AGENTS", "codex")

    assert _retry_stage() == "codex"


def test_valid_qwen_evidence_can_be_recovered_from_any_unfinished_state():
    assert _can_recover_qwen({"status": "interrupted"}) is True
    assert _can_recover_qwen({"status": "implementing"}) is True
    assert _can_recover_qwen({"status": "validating"}) is True
    assert _can_recover_qwen({"status": "accepted"}) is False
    assert _can_recover_qwen(None) is False


def test_recovered_qwen_pass_routes_to_codex_final_review():
    event = _recovered_qwen_event(
        WorkerResult(
            status=Status.PASS,
            summary="Recovered implementation",
            recommended_next_step=NextStep.COMPLETE,
        )
    )

    assert event.stage == "qwen"
    assert event.route == "codex_final"


def test_compact_run_summary_excludes_raw_worker_evidence(tmp_path):
    result = WorkerResult(
        status=Status.PASS,
        summary="Completed",
        recommended_next_step=NextStep.COMPLETE,
    )
    result.evidence.agent_log = "very large raw transcript"
    run = TaskRun(
        run_id="run-42",
        task=Task(task_id="M001", title="Mock"),
        status=Status.PASS,
        route="accepted",
        worker_results=[result],
        events=[
            RunEvent(
                stage="qwen",
                agent="Qwen",
                model="local",
                attempt=1,
                status=Status.PASS,
                summary="Completed",
                route="test",
                result=result,
            )
        ],
    )

    summary = _run_summary(run, Path(tmp_path) / "supervisor.sqlite3")

    assert "very large raw transcript" not in summary
    assert "supervisor-reports show run-42" in summary
    assert summary.startswith("SUCCESS — TASK ACCEPTED")


def test_completion_banner_marks_non_pass_runs_as_not_accepted():
    run = TaskRun(
        run_id="run-43",
        task=Task(task_id="D010", title="Release journey"),
        status=Status.NEEDS_USER_REVIEW,
        route="needs_user_review",
        worker_results=[],
        events=[],
    )

    assert _completion_banner(run).startswith("NOT ACCEPTED — USER REVIEW REQUIRED")


def test_failure_digest_extracts_concrete_test_failure_without_a_model(monkeypatch):
    result = WorkerResult(
        status=Status.REPAIRABLE_FAILURE,
        summary="Independent test worker failed: flutter test",
        test_result='Golden "goldens/settings.png": Pixel test failed, 9.34% diff detected.',
        recommended_next_step=NextStep.RETRY_QWEN,
    )
    run = TaskRun(
        run_id="run-failure",
        task=Task(task_id="D012", title="Typography"),
        status=Status.NEEDS_USER_REVIEW,
        route="needs_user_review",
        worker_results=[result],
        events=[RunEvent(stage="test", agent="tester", model="shell", attempt=1, status=result.status, summary=result.summary, route="user_review", result=result)],
    )
    monkeypatch.setenv("SUPERVISOR_FAILURE_SUMMARY_ENABLED", "false")

    digest = summarize_failure(run)

    assert "Golden" in digest
    assert "9.34%" in digest
    assert "deterministic fallback" in digest


def test_run_summary_prints_stored_failure_digest(tmp_path):
    run = TaskRun(
        run_id="run-digest",
        task=Task(task_id="D012", title="Typography"),
        status=Status.NEEDS_USER_REVIEW,
        route="needs_user_review",
        worker_results=[],
        notes=["Failure digest:\n- Golden image changed\nNext action: review and update approved baseline."],
    )

    assert "Golden image changed" in _run_summary(run, tmp_path / "supervisor.sqlite3")


def test_task_range_expands_inclusive_zero_padded_task_ids():
    assert _expand_task_range("D007-D010") == ["D007", "D008", "D009", "D010"]
    with pytest.raises(ValueError):
        _expand_task_range("D010-D007")
    with pytest.raises(ValueError):
        _expand_task_range("D007-T010")


def test_collection_runbooks_uses_declared_sequence_and_skips_supporting_markdown(tmp_path: Path):
    template = """---
task_id: {task_id}
sequence: {sequence}
title: Task
browser_impact: not_applicable
playwright_spec:
---

## Objective

Task

## Acceptance criteria

- It works.
"""
    (tmp_path / "F002.md").write_text(template.format(task_id="F002", sequence=2), encoding="utf-8")
    (tmp_path / "F001.md").write_text(template.format(task_id="F001", sequence=1), encoding="utf-8")
    (tmp_path / "README.md").write_text("supporting text", encoding="utf-8")
    (tmp_path / "PRODUCT_BRIEF.template.md").write_text("supporting text", encoding="utf-8")

    assert [path.stem for path in _collection_runbooks(tmp_path)] == ["F001", "F002"]


def test_dependency_gate_waits_for_unaccepted_r_series_prerequisites(monkeypatch, tmp_path: Path):
    r1, r2 = tmp_path / "R0001.md", tmp_path / "R0002.md"
    r1.touch()
    r2.touch()
    monkeypatch.setattr(cli, "load_task", lambda path: Task(task_id=path.stem, title="Task", dependencies=[] if path.stem == "R0001" else ["R0001"]))

    assert _unmet_dependencies([r1, r2], {"R0001": {"status": "accepted"}, "R0002": None}) == {}
    assert _unmet_dependencies([r1, r2], {"R0001": None, "R0002": None}) == {"R0002": ["R0001"]}


def test_collection_rediscovers_a_later_generated_g_wave(monkeypatch, tmp_path: Path):
    collection = tmp_path / "game-design-runbooks"
    collection.mkdir()
    template = "---\ntask_id: {task_id}\nsequence: {sequence}\ntitle: Design\nbrowser_impact: not_applicable\nplaywright_spec:\ndesign_authoring_batch: GB0001\n---\n\n## Objective\n\nDesign.\n\n## Acceptance criteria\n\n- Done.\n"
    (collection / "G0001.md").write_text(template.format(task_id="G0001", sequence=1), encoding="utf-8")
    database = tmp_path / ".state" / "game-design-runbooks.sqlite3"
    calls = []

    def run_batch(runbooks, *_args):
        calls.append([path.stem for path in runbooks])
        if calls == [["G0001"]]:
            (collection / "G0002.md").write_text(template.format(task_id="G0002", sequence=2), encoding="utf-8")
        store = RunStore(database)
        for path in runbooks:
            store.claim_task(path.stem, f"{path.stem}-run", 0)
            store.finish_task(TaskRun(task=Task(task_id=path.stem, title="Design"), run_id=f"{path.stem}-run", status=Status.PASS, route="accepted", worker_results=[]))
        store.close()
        return True

    monkeypatch.setattr(cli, "_run_task_range", run_batch)

    assert _run_collection_until_complete(collection, False, False, database, "# Brief") is True
    assert calls == [["G0001"], ["G0002"]]


def test_gb_writer_output_must_be_owned_by_its_design_batch(tmp_path: Path):
    collection = tmp_path / "game-design-runbooks"
    collection.mkdir()
    writer = collection / "GB0001.md"
    writer.write_text(
        "---\ntask_id: GB0001\nsequence: 1\ntitle: Write design tasks\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nWrite G files.\n\n## Output list\n\n- `G0001.md`\n\n## Acceptance criteria\n\n- G exists.\n",
        encoding="utf-8",
    )
    design = collection / "G0001.md"
    design.write_text(
        "---\ntask_id: G0001\nsequence: 2\ntitle: Design trivia\nbrowser_impact: not_applicable\nplaywright_spec:\ndesign_authoring_batch: GB0001\n---\n\n## Objective\n\nDesign trivia.\n\n## Acceptance criteria\n\n- Done.\n",
        encoding="utf-8",
    )
    assert _authoring_output_errors(writer) == []
    design.write_text(design.read_text(encoding="utf-8").replace("GB0001", "GB0002"), encoding="utf-8")
    assert _authoring_output_errors(writer) == ["G0001.md has design_authoring_batch GB0002"]


def test_game_design_completion_gate_requires_accepted_final_audit(tmp_path: Path):
    workspace = tmp_path / "project"
    planning = workspace / "planning"
    planning.mkdir(parents=True)
    specification = workspace / "specification"
    specification.mkdir()
    (specification / "02-game-design-bible.json").write_text(
        '{"selected_modules":["trivia"],"design_units":[{"id":"GAME-QUESTION-BANK","module":"trivia"}]}',
        encoding="utf-8",
    )
    (planning / "game-design-manifest.json").write_text(
        '{"status":"accepted","final_audit":{"task_id":"G0099","status":"accepted"},'
        '"modules":[{"module":"trivia","status":"accepted","design_output_paths":["specification/trivia.md"],"game_design_ids":["GAME-QUESTION-BANK"]}],'
        '"pending_modules":[],"blocked_modules":[]}',
        encoding="utf-8",
    )
    database = workspace / ".state" / "game-design-runbooks.sqlite3"
    assert cli._game_design_completion_error(workspace, database) == "G final audit G0099 is not accepted in durable state"

    store = RunStore(database)
    store.claim_task("G0099", "audit-run", 0)
    store.finish_task(TaskRun(task=Task(task_id="G0099", title="Audit"), run_id="audit-run", status=Status.PASS, route="accepted", worker_results=[]))
    store.close()
    assert cli._game_design_completion_error(workspace, database) is None

    (planning / "game-design-manifest.json").write_text(
        '{"status":"accepted","final_audit":{"task_id":"G0099","status":"accepted"},'
        '"modules":[{"module":"trivia","status":"accepted","design_output_paths":["specification/trivia.md"],"game_design_ids":[]}],'
        '"pending_modules":[],"blocked_modules":[]}',
        encoding="utf-8",
    )
    assert cli._game_design_completion_error(workspace, database) == "game-design manifest does not cover selected GAME-* units for trivia"


def test_invalid_accepted_authoring_task_is_reopened(tmp_path: Path):
    workspace = tmp_path / "project"
    authoring = workspace / "authoring-runbooks"
    authoring.mkdir(parents=True)
    b1 = authoring / "B0001.md"
    b1.write_text(
        "---\ntask_id: B0001\nsequence: 1\ntitle: Author\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nWrite R0001.\n\n## Output list\n\n- `../runbooks/R0001.md`\n\n## Acceptance criteria\n\n- R0001 exists.\n",
        encoding="utf-8",
    )
    database = workspace / ".state" / "authoring-runbooks.sqlite3"
    store = RunStore(database)
    store.claim_task("B0001", "run-1", 999999)
    store.finish_task(TaskRun(task=Task(task_id="B0001", title="Author"), run_id="run-1", status=Status.PASS, route="accepted", worker_results=[]))
    store.close()

    assert _authoring_output_errors(b1) == ["missing R0001.md"]
    assert _reopen_invalid_authoring_tasks([b1], {"B0001": {"status": "accepted"}}, database) == ["B0001"]
    assert RunStore(database).state_for("B0001")["status"] == "interrupted"


def test_initial_document_requires_and_loads_collection_context(tmp_path: Path):
    with pytest.raises(ValueError, match="INITIAL.md"):
        _initial_document(tmp_path)

    (tmp_path / "INITIAL.md").write_text("# Initial project brief\n\nBuild a task app.\n", encoding="utf-8")

    assert "Build a task app." in _initial_document(tmp_path)


def test_initial_document_uses_explicit_project_brief(tmp_path: Path):
    collection = tmp_path / "factory-runbooks"
    collection.mkdir()
    brief = tmp_path / "projects" / "task-app" / "INITIAL.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Initial project brief\n\nBuild a task app.\n", encoding="utf-8")

    assert "Build a task app." in _initial_document(collection, brief)


def test_project_workspace_uses_the_current_factory_directory(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "projects" / "task-app"
    workspace.mkdir(parents=True)
    (workspace / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _project_workspace("task-app") == workspace


def test_project_option_runs_the_factory_with_isolated_state(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "projects" / "task-app"
    workspace.mkdir(parents=True)
    (workspace / "INITIAL.md").write_text("# Brief\n\nBuild a task app.\n", encoding="utf-8")
    factory = tmp_path / "runbooks"
    factory.mkdir()
    (factory / "F001.md").write_text(
        "---\ntask_id: F001\nsequence: 1\ntitle: Factory\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nDo it.\n\n## Acceptance criteria\n\n- Done.\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["supervisor-run", "--project", "task-app"])
    monkeypatch.setattr(cli, "_run_collection_until_complete", lambda *args: calls.append(args) or True)
    monkeypatch.setattr(cli, "_run_registered_collections", lambda *args: calls.append(args))

    cli.main()

    assert calls[0][0] == factory
    assert calls[0][3] == workspace / ".state" / "factory.sqlite3"
    assert "Build a task app." in calls[0][4]
    assert calls[1][3] == workspace


def test_project_option_allows_a_targeted_recovery_stage(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "projects" / "task-app"
    workspace.mkdir(parents=True)
    (workspace / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    (workspace / ".env").write_text("SUPERVISOR_REPO_ROOT=../..\n", encoding="utf-8")
    factory = tmp_path / "runbooks"
    factory.mkdir()
    (factory / "F001.md").write_text(
        "---\ntask_id: F001\nsequence: 1\ntitle: Factory\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nDo it.\n\n## Acceptance criteria\n\n- Done.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUPERVISOR_OBSERVABILITY_ENABLED", "false")
    monkeypatch.setattr(sys, "argv", ["supervisor-run", "--project", "task-app", "--task-id", "F001", "--start-on", "test", "--dry-run"])

    cli.main()

    state = RunStore(workspace / ".state" / "factory.sqlite3").state_for("F001")
    assert state["status"] == "needs_user_review"


def test_interrupted_project_factory_resumes_the_first_unaccepted_task(monkeypatch, tmp_path: Path):
    factory = tmp_path / "runbooks"
    factory.mkdir()
    template = "---\ntask_id: {task_id}\nsequence: {sequence}\ntitle: Task\nbrowser_impact: not_applicable\nplaywright_spec:\n---\n\n## Objective\n\nDo it.\n\n## Acceptance criteria\n\n- Done.\n"
    (factory / "F001.md").write_text(template.format(task_id="F001", sequence=1), encoding="utf-8")
    (factory / "F002.md").write_text(template.format(task_id="F002", sequence=2), encoding="utf-8")
    database = tmp_path / "projects" / "task-app" / ".state" / "factory.sqlite3"
    store = RunStore(database)
    store.claim_task("F001", "interrupted-run", 0)
    store.abandon_task("F001", "interrupted-run", "Stopped while implementing F001")
    store.close()
    batches = []
    monkeypatch.setattr(cli, "_run_task_range", lambda runbooks, *_args: batches.append(runbooks) or False)

    assert _run_collection_until_complete(factory, False, False, database, "# Brief") is False
    assert [path.stem for path in batches[0]] == ["F001", "F002"]


def test_initial_document_uses_a_generated_project_brief_when_no_local_initial_exists(tmp_path: Path):
    generated_collection = tmp_path / "authoring-runbooks"
    generated_collection.mkdir()
    (tmp_path / "PROJECT_BRIEF.md").write_text("# Project brief\n\nBuild a task app.\n", encoding="utf-8")

    assert "Build a task app." in _initial_document(generated_collection)


def test_targeted_project_b_or_r_retry_uses_its_child_collection_database(tmp_path: Path):
    workspace = tmp_path / "projects" / "task-app"
    authoring = workspace / "authoring-runbooks" / "B0002.md"
    product = workspace / "runbooks" / "R0002.md"
    factory = workspace / ".state" / "factory.sqlite3"
    authoring.parent.mkdir(parents=True)
    product.parent.mkdir(parents=True)

    assert _project_database_for_runbook(workspace, authoring, factory) == workspace / ".state" / "authoring-runbooks.sqlite3"
    assert _project_database_for_runbook(workspace, product, factory) == workspace / ".state" / "runbooks.sqlite3"
    assert _project_database_for_runbook(workspace, tmp_path / "runbooks" / "F001.md", factory) == factory


def test_registered_collections_follow_explicit_children_recursively(tmp_path: Path, monkeypatch):
    parent = tmp_path / "source-runbooks"
    workspace = tmp_path / "project"
    authoring = workspace / "authoring-runbooks"
    implementation = workspace / "runbooks"
    for directory in (parent / ".supervisor-children", authoring / ".supervisor-children", implementation):
        directory.mkdir(parents=True, exist_ok=True)
    (parent / ".supervisor-children" / "authoring.json").write_text(
        '{"runbooks_dir": "../project/authoring-runbooks"}', encoding="utf-8"
    )
    (authoring / ".supervisor-children" / "implementation.json").write_text(
        '{"runbooks_dir": "../runbooks"}', encoding="utf-8"
    )
    (authoring / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    (implementation / "INITIAL.md").write_text("# Brief\n", encoding="utf-8")
    calls = []

    def run_collection(directory, dry_run, continue_on_nonpass, database_path, initial_context, repo_root=None):
        calls.append((directory, database_path, initial_context))
        return True

    monkeypatch.setattr(cli, "_run_collection_until_complete", run_collection)

    _run_registered_collections(parent, dry_run=False, continue_on_nonpass=False, project_workspace=workspace)

    assert [call[0] for call in calls] == [authoring, implementation]
    assert all(call[1] == workspace / ".state" / f"{call[0].name}.sqlite3" for call in calls)
