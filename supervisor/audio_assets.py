"""Product-owned, reproducible local music and sound-effect helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import Task


def audio_setting(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def audio_style_name() -> str:
    return audio_setting("AUDIO_STYLE_NAME", "original product audio")


def audio_style_prompt() -> str:
    return audio_setting(
        "AUDIO_STYLE_PROMPT",
        "original instrumental game music, clear looping structure, no vocals, no recognisable melody",
    )


def audio_root(repo_root: Path, audio_id: str) -> Path:
    return repo_root / "assets" / "audio" / "generated" / audio_id


def manifest_path(repo_root: Path, audio_id: str) -> Path:
    return audio_root(repo_root, audio_id) / "manifest.json"


def load_manifest(repo_root: Path, audio_id: str) -> dict:
    return json.loads(manifest_path(repo_root, audio_id).read_text(encoding="utf-8"))


def save_manifest(repo_root: Path, audio_id: str, manifest: dict) -> None:
    root = audio_root(repo_root, audio_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path(repo_root, audio_id).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def audio_ids(task: Task) -> list[str]:
    return task.audio_ids


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
