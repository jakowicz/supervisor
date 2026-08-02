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
from urllib.error import URLError
from urllib.request import Request, urlopen

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


def resolved_art_direction(product_context: str) -> tuple[str, str]:
    """Return the configured style, or let the local Gemma model author one.

    The automatic path is deliberately local, concise, and fails closed to the
    safe generic prompt if Ollama is unavailable. It never asks a model to
    emulate a named artist, studio, or commercial product.
    """

    if art_setting("ART_DIRECTION_MODE", "gemma4_auto") != "gemma4_auto":
        return style_name(), style_prompt()
    model = art_setting("ART_DIRECTION_MODEL", "gemma4:12b")
    prompt = (
        "Write one concise original visual-art direction for a new product asset. "
        "Use only the product context below. Specify medium, palette, lighting, shape language, "
        "and readability. Do not mention or imitate artists, studios, franchises, brands, or existing products. "
        "Do not include a logo or text. Return the direction only.\n\n"
        f"Product context: {product_context[:5000]}"
    )
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.4}}
    try:
        base_url = art_setting("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        request = Request(f"{base_url}/api/generate", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=int(art_setting("ART_DIRECTION_TIMEOUT_SECONDS", "120"))) as response:
            direction = str(json.loads(response.read().decode("utf-8"))["response"]).strip()
        if direction:
            return f"Gemma-generated original art direction ({model})", direction
    except (OSError, KeyError, ValueError, URLError, json.JSONDecodeError):
        pass
    return style_name(), style_prompt()


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
