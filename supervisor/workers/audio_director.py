"""Create the durable music/SFX cue manifest before any generation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..audio_assets import audio_ids, audio_root, audio_style_name, audio_style_prompt, save_manifest
from ..models import Evidence, NextStep, Status, Task, WorkerResult


def run(task: Task, repo_root: Path) -> WorkerResult:
    if not task.audio_ids or not task.audio_brief or task.audio_duration_seconds < 1:
        return WorkerResult(status=Status.NEEDS_USER_REVIEW, summary="Audio-impacting runbook must declare audio_ids, audio_brief, and a positive audio_duration_seconds.", recommended_next_step=NextStep.ASK_USER)
    created: list[str] = []
    for audio_id in audio_ids(task):
        root = audio_root(repo_root, audio_id)
        root.mkdir(parents=True, exist_ok=True)
        prompt = ", ".join(part for part in (audio_style_prompt(), task.audio_brief, task.title, f"cue: {audio_id.replace('_', ' ')}") if part)
        manifest = {
            "audio_id": audio_id,
            "task_id": task.task_id,
            "audio_style_version": task.audio_style_version or "project-audio-v1",
            "style_name": audio_style_name(),
            "prompt": prompt[:6000],
            "duration_seconds": task.audio_duration_seconds,
            "loop": task.audio_loop == "required",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provenance": {"original_only": True, "commercial_reference_prohibited": True},
            "generation": {"backend": "ACE-Step 1.5 XL Turbo", "status": "briefed"},
        }
        save_manifest(repo_root, audio_id, manifest)
        created.append(str((root / "manifest.json").relative_to(repo_root)))
    return WorkerResult(status=Status.PASS, summary=f"Created cue manifests for {len(created)} audio asset(s).", changed_files=created, evidence=Evidence(agent_log="\n".join(created)), recommended_next_step=NextStep.COMPLETE)
