"""Create a deterministic original-art brief and reproducibility manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..art_assets import asset_ids, asset_root, negative_prompt, resolved_art_direction, save_manifest
from ..models import Evidence, NextStep, Status, Task, WorkerResult


def run(task: Task, repo_root: Path) -> WorkerResult:
    if not task.asset_ids:
        return WorkerResult(
            status=Status.NEEDS_USER_REVIEW,
            summary="Asset-impacting runbook must declare asset_ids in its metadata.",
            recommended_next_step=NextStep.ASK_USER,
        )
    brief_reference = repo_root / task.asset_brief if task.asset_brief else None
    brief_text = brief_reference.read_text(encoding="utf-8") if brief_reference and brief_reference.is_file() else ""
    direction_name, direction_prompt = resolved_art_direction(
        "\n".join(part for part in (task.title, task.objective, brief_text) if part)
    )
    created: list[str] = []
    for asset_id in asset_ids(task):
        root = asset_root(repo_root, asset_id)
        root.mkdir(parents=True, exist_ok=True)
        # A task can request multiple progression stages.  Give the local model
        # the asset identifier as an explicit subject cue so those candidates
        # do not all receive an otherwise identical task-level prompt.
        asset_cue = f"Requested source asset: {asset_id.replace('_', ' ')}"
        prompt = ", ".join(part for part in (direction_prompt, task.title, asset_cue, task.objective.replace("\n", " "), brief_text.replace("\n", " ")) if part)
        manifest = {
            "asset_id": asset_id,
            "task_id": task.task_id,
            "visual_style_version": task.visual_style_version or "project-v1",
            "style_name": direction_name,
            "prompt": prompt[:6000],
            "negative_prompt": negative_prompt(),
            "brief_reference": task.asset_brief,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provenance": {"original_only": True, "protected_ip_reference_prohibited": True},
            "generation": {"backend": "ComfyUI", "model": "Z-Image-Turbo", "status": "briefed"},
        }
        save_manifest(repo_root, asset_id, manifest)
        created.append(str((root / "manifest.json").relative_to(repo_root)))
    return WorkerResult(
        status=Status.PASS,
        summary=f"Created original product briefs for {len(created)} asset(s).",
        changed_files=created,
        evidence=Evidence(agent_log="\n".join(created)),
        recommended_next_step=NextStep.COMPLETE,
    )
