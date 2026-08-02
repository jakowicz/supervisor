"""Verify selected local audio assets and their reproducibility manifests."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from supervisor.audio_assets import audio_ids, load_manifest, sha256
from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict
from worker_adapter import emit, repository_root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    args = parser.parse_args()
    task = Task.model_validate_json(Path(args.task_file).read_text(encoding="utf-8"))
    root, errors, checked = repository_root(), [], []
    for audio_id in audio_ids(task):
        try:
            manifest = load_manifest(root, audio_id)
            selected = root / manifest["selected"]["path"]
            if not selected.is_file() or selected.stat().st_size < 1024:
                errors.append(f"{audio_id}: production audio is missing or empty")
            elif manifest["selected"].get("sha256") != sha256(selected):
                errors.append(f"{audio_id}: production audio hash does not match manifest")
            elif not manifest.get("provenance", {}).get("original_only"):
                errors.append(f"{audio_id}: original-audio provenance is not recorded")
            else:
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(selected)], capture_output=True, text=True, check=False)
                duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0.0
                if duration < max(1.0, manifest["duration_seconds"] * 0.8):
                    errors.append(f"{audio_id}: duration {duration:.1f}s is materially shorter than requested {manifest['duration_seconds']}s")
                else:
                    checked.append(str(selected.relative_to(root)))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{audio_id}: {error}")
    if errors:
        emit(WorkerResult(status=Status.REPAIRABLE_FAILURE, summary="Audio QA rejected generated audio.", test_result="\n".join(errors), recommended_next_step=NextStep.ASK_USER))
        return
    emit(WorkerResult(status=Status.PASS, summary=f"Audio QA passed for {len(checked)} original production audio asset(s).", evidence=Evidence(screenshots=checked), recommended_next_step=NextStep.COMPLETE))


if __name__ == "__main__":
    main()
