"""Local, reproducible asset-lane helpers.

The supervisor stores only paths and metadata in its evidence ledger.  The
selected source assets live with the product so a Git commit can reproduce the
game; discarded candidates remain ignored local working material.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import Task


def art_setting(key: str, default: str = "") -> str:
    """Read project-owned art direction without hard-coding a product identity."""

    return os.getenv(key, default).strip()


def style_name() -> str:
    return art_setting("ART_STYLE_NAME", "original game-art style")


def style_prompt() -> str:
    return art_setting(
        "ART_STYLE_PROMPT",
        "original game asset, clear readable silhouette, premium hand-painted illustration, no text, no logo",
    )


def negative_prompt() -> str:
    return art_setting(
        "ART_NEGATIVE_PROMPT",
        "copied commercial game art, trademark, logo, watermark, text, UI screenshot, blurry, duplicate object",
    )


def product_slug() -> str:
    return art_setting("ART_PRODUCT_SLUG", "project").lower().replace(" ", "-")


def protected_ip_terms() -> tuple[str, ...]:
    return tuple(term.strip().lower() for term in art_setting("ART_PROTECTED_IP_TERMS").split(",") if term.strip())


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
