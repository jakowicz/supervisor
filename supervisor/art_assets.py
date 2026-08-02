"""Local, reproducible asset-lane helpers.

The supervisor stores only paths and metadata in its evidence ledger.  The
selected source assets live with the product so a Git commit can reproduce the
game; discarded candidates remain ignored local working material.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import Task


STYLE_NAME = "Emberhold lanternlit hearth-fantasy"
STYLE_PROMPT = (
    "original Emberhold lanternlit hearth-fantasy game art, tactile carved stone, "
    "weathered warm timber, aged brass, rich kiln-charcoal shadows, ember orange "
    "and moss-teal accents, jewel-toned focal colour, bold readable silhouette, "
    "hand-painted illustration, premium mobile strategy game asset, no text, no logo"
)
NEGATIVE_PROMPT = (
    "Clash of Clans, Supercell, copied game art, trademark, logo, watermark, text, "
    "UI screenshot, blurry, photorealistic, thin unreadable silhouette, duplicate object"
)


def asset_root(repo_root: Path, asset_id: str) -> Path:
    return repo_root / "assets" / "generated" / asset_id


def manifest_path(repo_root: Path, asset_id: str) -> Path:
    return asset_root(repo_root, asset_id) / "manifest.json"


def load_manifest(repo_root: Path, asset_id: str) -> dict:
    return json.loads(manifest_path(repo_root, asset_id).read_text(encoding="utf-8"))


def save_manifest(repo_root: Path, asset_id: str, manifest: dict) -> None:
    root = asset_root(repo_root, asset_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path(repo_root, asset_id).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def asset_ids(task: Task) -> list[str]:
    return task.asset_ids



def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
