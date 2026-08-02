"""Generate local Emberhold art candidates through ComfyUI's HTTP API.

Uses only the already-installed Z-Image Turbo split model and writes every
workflow, seed, filename and source hash into the product asset manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from supervisor.art_assets import asset_ids, asset_root, load_manifest, save_manifest, sha256
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
try:  # Direct script execution from `scripts/`.
    from worker_adapter import emit, repository_root
except ModuleNotFoundError:  # Importing the workflow from the test suite.
    from scripts.worker_adapter import emit, repository_root


def _request(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def workflow(prompt: str, seed: int, filename_prefix: str) -> dict:
    """Official Z-Image Turbo architecture, expressed in Comfy API format."""

    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": int(os.getenv("COMFYUI_ASSET_WIDTH", "1024")), "height": int(os.getenv("COMFYUI_ASSET_HEIGHT", "1024")), "batch_size": int(os.getenv("COMFYUI_ASSET_CANDIDATES", "4"))}},
        "7": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["7", 0], "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": filename_prefix}},
    }


def wait_for_history(base_url: str, prompt_id: str) -> dict:
    deadline = time.monotonic() + int(os.getenv("COMFYUI_GENERATION_TIMEOUT_SECONDS", "600"))
    while time.monotonic() < deadline:
        history = _request(f"{base_url}/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(1)
    raise TimeoutError("ComfyUI generation did not finish before its configured timeout")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    base_url = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
    changed: list[str] = []
    screenshots: list[str] = []
    try:
        _request(f"{base_url}/system_stats")
        for ordinal, asset_id in enumerate(asset_ids(task), start=1):
            manifest = load_manifest(repo_root, asset_id)
            seed = int(os.getenv("COMFYUI_ASSET_SEED", "104729")) + ordinal
            prefix = f"emberhold/{task.task_id.lower()}/{asset_id}"
            graph = workflow(manifest["prompt"], seed, prefix)
            workflow_path = root = asset_root(repo_root, asset_id)
            workflow_path = root / "workflow.json"
            workflow_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            queued = _request(f"{base_url}/prompt", {"prompt": graph})
            prompt_id = queued["prompt_id"]
            history = wait_for_history(base_url, prompt_id)
            outputs = history.get("outputs", {}).get("10", {}).get("images", [])
            if not outputs:
                raise RuntimeError(f"ComfyUI produced no saved image for {asset_id}: {history.get('status', {})}")
            candidates = root / "candidates"
            candidates.mkdir(parents=True, exist_ok=True)
            copied: list[str] = []
            for index, image in enumerate(outputs, start=1):
                query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")})
                target = candidates / f"candidate-{index:02d}.png"
                with urllib.request.urlopen(f"{base_url}/view?{query}", timeout=60) as response, target.open("wb") as output:
                    shutil.copyfileobj(response, output)
                copied.append(str(target.relative_to(repo_root)))
            manifest["generation"] = {
                "backend": "ComfyUI",
                "base_url": base_url,
                "model": "Z-Image-Turbo",
                "seed": seed,
                "workflow": str(workflow_path.relative_to(repo_root)),
                "prompt_id": prompt_id,
                "status": "generated",
                "candidates": [{"path": item, "sha256": sha256(repo_root / item)} for item in copied],
            }
            save_manifest(repo_root, asset_id, manifest)
            changed.extend([str((root / "manifest.json").relative_to(repo_root)), str(workflow_path.relative_to(repo_root)), *copied])
            screenshots.extend(copied)
    except (OSError, KeyError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as error:
        emit(WorkerResult(status=Status.ENVIRONMENT_FAILURE, summary=f"ComfyUI asset generation could not complete: {error}", evidence=Evidence(test_log=str(error)), recommended_next_step=NextStep.ASK_USER))
        return
    emit(WorkerResult(status=Status.PASS, summary=f"Generated local Z-Image Turbo candidate sets for {len(asset_ids(task))} asset(s).", changed_files=changed, evidence=Evidence(screenshots=screenshots), recommended_next_step=NextStep.COMPLETE))


if __name__ == "__main__":
    main()
