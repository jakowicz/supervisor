import json

from scripts.comfy_asset_generator import workflow
from supervisor.models import NextStep, Status, Task, WorkerResult
from supervisor.routing import first_stage, next_route
from supervisor.workers.art_director import run as create_art_brief


def result(status: Status = Status.PASS) -> WorkerResult:
    return WorkerResult(status=status, summary="test", recommended_next_step=NextStep.COMPLETE)


def art_task() -> Task:
    return Task(task_id="A001", title="Original gate", asset_impact="required", asset_ids=["ember_gate_001"])


def test_asset_pipeline_is_strictly_opt_in():
    assert first_stage(Task(task_id="D001", title="code only")) == "qwen"
    assert first_stage(art_task()) == "art_director"


def test_art_pipeline_runs_before_implementation():
    task = art_task()
    assert next_route("art_director", result(), "art_director", {}, task) == "asset_generator"
    assert next_route("asset_generator", result(), "art_director", {}, task) == "asset_finisher"
    assert next_route("asset_finisher", result(), "art_director", {}, task) == "asset_qa"
    assert next_route("asset_qa", result(), "art_director", {}, task) == "qwen"


def test_art_failure_stops_for_evidence_instead_of_retrying_a_coder():
    assert next_route("asset_qa", result(Status.REPAIRABLE_FAILURE), "art_director", {}, art_task()) == "user_review"


def test_art_director_records_original_art_provenance(tmp_path):
    result = create_art_brief(art_task(), tmp_path)

    manifest = json.loads((tmp_path / "assets/generated/ember_gate_001/manifest.json").read_text(encoding="utf-8"))
    assert result.status is Status.PASS
    assert manifest["provenance"]["original_only"] is True
    assert "copied commercial game art" in manifest["negative_prompt"]
    assert manifest["generation"]["model"] == "Z-Image-Turbo"
    assert "Requested source asset: ember gate 001" in manifest["prompt"]


def test_z_image_workflow_records_the_installed_split_model_contract():
    graph = workflow("original game gate", 104730, "project/test/gate")

    assert graph["1"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors"
    assert graph["2"]["inputs"]["clip_name"] == "qwen_3_4b.safetensors"
    assert graph["3"]["inputs"]["vae_name"] == "ae.safetensors"
    assert graph["6"]["inputs"]["batch_size"] == 4
