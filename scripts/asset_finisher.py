"""Promote a deterministic, source-preserving production asset from candidates."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from supervisor.art_assets import asset_ids, asset_root, load_manifest, save_manifest, sha256
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from worker_adapter import repository_root
import json


def png_dimensions(path: Path) -> tuple[int, int] | None:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    changed: list[str] = []
    try:
        for asset_id in asset_ids(task):
            root = asset_root(repo_root, asset_id)
            candidates = sorted((root / "candidates").glob("candidate-*.png"))
            if not candidates:
                raise RuntimeError(f"{asset_id} has no candidate images")
            selected = root / "selected.png"
            shutil.copy2(candidates[0], selected)
            dimensions = png_dimensions(selected)
            if not dimensions or min(dimensions) < 512:
                raise RuntimeError(f"{asset_id} selected image is not a usable 512px+ PNG")
            manifest = load_manifest(repo_root, asset_id)
            manifest["selected"] = {"path": str(selected.relative_to(repo_root)), "sha256": sha256(selected), "dimensions": list(dimensions), "selection_policy": "first technically valid Z-Image Turbo candidate; automated promotion enabled"}
            manifest["generation"]["status"] = "finished"
            save_manifest(repo_root, asset_id, manifest)
            changed.extend([str(selected.relative_to(repo_root)), str((root / "manifest.json").relative_to(repo_root))])
    except (OSError, RuntimeError, KeyError) as error:
        print(json.dumps(model_to_dict(WorkerResult(status=Status.REPAIRABLE_FAILURE, summary=f"Asset finishing failed: {error}", evidence=Evidence(test_log=str(error)), recommended_next_step=NextStep.ASK_USER))))
        return
    print(json.dumps(model_to_dict(WorkerResult(status=Status.PASS, summary=f"Finished {len(asset_ids(task))} production asset(s) from local candidates.", changed_files=changed, recommended_next_step=NextStep.COMPLETE))))


if __name__ == "__main__":
    main()
