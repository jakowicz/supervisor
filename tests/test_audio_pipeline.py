import json

from supervisor.models import NextStep, Status, Task, WorkerResult
from supervisor.routing import first_stage, next_route
from supervisor.workers.audio_director import run as create_audio_brief


def result(status: Status = Status.PASS) -> WorkerResult:
    return WorkerResult(status=status, summary="test", recommended_next_step=NextStep.COMPLETE)


def audio_task() -> Task:
    return Task(
        task_id="AUDIO-001",
        title="Create the village menu cue",
        objective="Give the title screen an original sense of place.",
        audio_impact="required",
        audio_ids=["ember_village_menu_loop"],
        audio_brief="Thirty-second seamless instrumental menu cue with warmth and anticipation; no vocals.",
        audio_duration_seconds=30,
        audio_loop="required",
        audio_style_version="ember-audio-v1",
    )


def test_audio_pipeline_is_strictly_opt_in_and_runs_before_implementation():
    assert first_stage(Task(task_id="D001", title="code only")) == "qwen"
    assert first_stage(audio_task()) == "audio_director"
    task = audio_task()
    assert next_route("audio_director", result(), "audio_director", {}, task) == "audio_generator"
    assert next_route("audio_generator", result(), "audio_director", {}, task) == "audio_qa"
    assert next_route("audio_qa", result(), "audio_director", {}, task) == "qwen"


def test_visual_and_audio_pipelines_are_both_completed_before_coding():
    task = audio_task().model_copy(update={"asset_impact": "required", "asset_ids": ["ember_title_gate"]})

    assert first_stage(task) == "art_director"
    assert next_route("asset_qa", result(), "art_director", {}, task) == "audio_director"
    assert next_route("audio_qa", result(), "art_director", {}, task) == "qwen"


def test_audio_failure_stops_for_evidence_instead_of_retrying_a_coder():
    assert next_route("audio_qa", result(Status.REPAIRABLE_FAILURE), "audio_director", {}, audio_task()) == "user_review"


def test_audio_director_records_project_direction_cue_and_generation_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_STYLE_NAME", "moonlit chamber fantasy")
    monkeypatch.setenv("AUDIO_STYLE_PROMPT", "wooden flute, chamber strings, gentle hand percussion, no vocals")

    created = create_audio_brief(audio_task(), tmp_path)
    manifest = json.loads(
        (tmp_path / "assets/audio/generated/ember_village_menu_loop/manifest.json").read_text(encoding="utf-8")
    )

    assert created.status is Status.PASS
    assert manifest["style_name"] == "moonlit chamber fantasy"
    assert "wooden flute, chamber strings" in manifest["prompt"]
    assert "Thirty-second seamless instrumental menu cue" in manifest["prompt"]
    assert manifest["duration_seconds"] == 30
    assert manifest["loop"] is True
    assert manifest["audio_style_version"] == "ember-audio-v1"
    assert manifest["provenance"]["original_only"] is True
    assert manifest["generation"] == {"backend": "ACE-Step 1.5 XL Turbo", "status": "briefed"}
