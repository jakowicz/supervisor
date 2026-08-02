"""Reject malformed, non-reproducible or protected-IP-referencing art assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supervisor.art_assets import asset_ids, asset_root, load_manifest, sha256
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from worker_adapter import repository_root


def is_png(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    checked: list[str] = []
    errors: list[str] = []
    for asset_id in asset_ids(task):
        try:
            manifest = load_manifest(repo_root, asset_id)
            selected = repo_root / manifest["selected"]["path"]
            forbidden = ("clash of clans", "supercell")
            if not is_png(selected):
                errors.append(f"{asset_id}: selected production image is missing or not PNG")
            elif manifest["selected"].get("sha256") != sha256(selected):
                errors.append(f"{asset_id}: selected image hash does not match manifest")
            elif any(term in manifest.get("prompt", "").lower() for term in forbidden):
                errors.append(f"{asset_id}: prohibited protected-IP reference in generation prompt")
            elif not manifest.get("provenance", {}).get("original_only"):
                errors.append(f"{asset_id}: original-art provenance not recorded")
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
