"""Reject malformed, non-reproducible or protected-IP-referencing art assets."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from supervisor.art_assets import asset_ids, asset_root, load_manifest, protected_ip_terms, sha256, style_name, style_prompt
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from worker_adapter import repository_root


def is_png(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def vision_review(path: Path) -> tuple[bool, str]:
    """Apply a narrow local visual rubric; never upload the asset remotely."""

    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("LOCAL_VISION_MODEL", "gemma4:12b")
    prompt = (
        f"You are the local art QA gate for {style_name()}. Review this single generated "
        f"game asset against this original style: {style_prompt()}. Reject copied or "
        "recognisably branded game art, visible text/logos/watermarks, extra limbs, "
        "unreadable silhouette, obvious deformation, severe crop or low-quality blur. "
        "Return JSON only: {\"decision\":\"pass\"|\"fail\",\"summary\":string,\"flags\":[string]}."
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(path.read_bytes()).decode("ascii")]}],
        "options": {"temperature": 0},
    }
    try:
        request = Request(f"{base_url}/api/chat", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=int(os.getenv("ASSET_VISION_TIMEOUT_SECONDS", "180"))) as response:
            body = json.loads(response.read().decode("utf-8"))
        verdict = json.loads(body["message"]["content"])
        decision = verdict.get("decision")
        if decision not in {"pass", "fail"}:
            return False, "local vision reviewer returned no valid pass/fail decision"
        summary = str(verdict.get("summary", "no summary"))
        flags = verdict.get("flags", [])
        return decision == "pass", f"{summary}; flags: {', '.join(map(str, flags)) or 'none'}"
    except (OSError, KeyError, ValueError, URLError, json.JSONDecodeError) as error:
        return False, f"local vision reviewer unavailable or invalid: {error}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    checked: list[str] = []
    errors: list[str] = []
    # A retained large coding model can make a second vision model swap slowly
    # or expensively on a local machine.  Technical/provenance QA is mandatory;
    # vision review is an explicit art-session choice.
    vision_enabled = os.getenv("ASSET_VISION_QA_ENABLED", "false").lower() == "true"
    for asset_id in asset_ids(task):
        try:
            manifest = load_manifest(repo_root, asset_id)
            selected = repo_root / manifest["selected"]["path"]
            forbidden = protected_ip_terms()
            if not is_png(selected):
                errors.append(f"{asset_id}: selected production image is missing or not PNG")
            elif manifest["selected"].get("sha256") != sha256(selected):
                errors.append(f"{asset_id}: selected image hash does not match manifest")
            elif not manifest.get("style_name") or not manifest.get("prompt") or not manifest.get("visual_style_version"):
                errors.append(f"{asset_id}: manifest is missing the approved art direction, prompt, or style version")
            elif any(term in manifest.get("prompt", "").lower() for term in forbidden):
                errors.append(f"{asset_id}: prohibited protected-IP reference in generation prompt")
            elif not manifest.get("provenance", {}).get("original_only"):
                errors.append(f"{asset_id}: original-art provenance not recorded")
            elif vision_enabled:
                passed, review = vision_review(selected)
                if not passed:
                    errors.append(f"{asset_id}: local visual QA rejected it: {review}")
                else:
                    checked.append(str(selected.relative_to(repo_root)))
            else:
                checked.append(str(selected.relative_to(repo_root)))
        except (OSError, KeyError, json.JSONDecodeError) as error:
            errors.append(f"{asset_id}: {error}")
    if errors:
        print(json.dumps(model_to_dict(WorkerResult(status=Status.REPAIRABLE_FAILURE, summary="Asset QA rejected generated assets.", test_result="\n".join(errors), recommended_next_step=NextStep.ASK_USER))))
        return
    print(json.dumps(model_to_dict(WorkerResult(status=Status.PASS, summary=f"Asset QA passed for {len(checked)} original, reproducible production asset(s).", evidence=Evidence(screenshots=checked), recommended_next_step=NextStep.COMPLETE))))


if __name__ == "__main__":
    main()
