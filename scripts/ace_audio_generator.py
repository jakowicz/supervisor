"""Generate product-owned music locally through the ACE-Step REST service."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from supervisor.audio_assets import audio_ids, audio_root, load_manifest, save_manifest, sha256
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from worker_adapter import emit, repository_root


def request(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    args = parser.parse_args()
    task = Task.model_validate_json(Path(args.task_file).read_text(encoding="utf-8"))
    root = repository_root()
    base_url = os.getenv("ACE_STEP_API_URL", "http://127.0.0.1:8001").rstrip("/")
    model = os.getenv("ACE_STEP_MODEL", "acestep-v15-xl-turbo")
    deadline = time.monotonic() + int(os.getenv("ACE_STEP_GENERATION_TIMEOUT_SECONDS", "1800"))
    changed: list[str] = []
    try:
        request(f"{base_url}/health")
        for audio_id in audio_ids(task):
            manifest = load_manifest(root, audio_id)
            submitted = request(f"{base_url}/release_task", {
                "prompt": manifest["prompt"], "lyrics": "[instrumental]", "thinking": False,
                "model": model, "audio_duration": manifest["duration_seconds"], "audio_format": "wav",
                "inference_steps": int(os.getenv("ACE_STEP_INFERENCE_STEPS", "8")),
                "use_random_seed": True, "task_type": "text2music",
            })
            job_id = submitted.get("data", {}).get("task_id") or submitted.get("data", {}).get("id")
            if not job_id:
                raise RuntimeError(f"ACE-Step did not return a task id: {submitted}")
            output_url = ""
            while time.monotonic() < deadline:
                result = request(f"{base_url}/query_result", {"task_id_list": [job_id]})
                item = (result.get("data") or [{}])[0]
                status = item.get("status")
                paths = item.get("audio_paths") or item.get("result", {}).get("audio_paths") or []
                if paths:
                    output_url = paths[0]
                    break
                if status in {2, "failed", "error"}:
                    raise RuntimeError(f"ACE-Step generation failed: {item}")
                time.sleep(2)
            if not output_url:
                raise TimeoutError("ACE-Step did not finish before ACE_STEP_GENERATION_TIMEOUT_SECONDS")
            target = audio_root(root, audio_id) / "production.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            url = output_url if output_url.startswith("http") else f"{base_url}{output_url}"
            with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            manifest["generation"] = {"backend": "ACE-Step 1.5", "model": model, "api_url": base_url, "job_id": job_id, "status": "generated"}
            manifest["selected"] = {"path": str(target.relative_to(root)), "sha256": sha256(target)}
            save_manifest(root, audio_id, manifest)
            changed.extend([str(target.relative_to(root)), str((audio_root(root, audio_id) / "manifest.json").relative_to(root))])
    except (OSError, ValueError, KeyError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as error:
        emit(WorkerResult(status=Status.ENVIRONMENT_FAILURE, summary=f"ACE-Step audio generation could not complete: {error}", evidence=Evidence(test_log=str(error)), recommended_next_step=NextStep.ASK_USER))
        return
    emit(WorkerResult(status=Status.PASS, summary=f"Generated {len(audio_ids(task))} local ACE-Step production audio asset(s).", changed_files=changed, evidence=Evidence(screenshots=changed), recommended_next_step=NextStep.COMPLETE))


if __name__ == "__main__":
    main()
