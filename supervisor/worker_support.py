"""Pure configuration helpers shared by coding-worker adapters."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit, urlunsplit

from .models import WorkerResult


def openhands_base_url(model: str, base_url: str | None) -> str | None:
    """Return the native Ollama URL required by OpenHands' Ollama provider.

    Qwen Code uses Ollama's OpenAI-compatible ``/v1`` endpoint. OpenHands uses
    LiteLLM's native ``ollama/...`` provider, whose requests target ``/api`` and
    therefore must start at the Ollama root instead.
    """

    if not base_url or not model.startswith("ollama/"):
        return base_url
    parsed = urlsplit(base_url)
    if parsed.path.rstrip("/") != "/v1":
        return base_url
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def prepare_openhands_persistence(
    source_dir: Path, target_dir: Path, reasoning_effort: str = "none"
) -> Path:
    """Create the isolated OpenHands profile used by one supervisor project.

    OpenHands' CLI override flag only accepts model, URL, and API key. Its
    stored profile otherwise defaults to high reasoning effort, which LiteLLM
    maps into an Ollama thinking request. Seed a project-owned profile once,
    preserve its MCP configuration, and set the safe local-model default.
    """

    source_settings = source_dir / "agent_settings.json"
    target_settings = target_dir / "agent_settings.json"
    if not target_settings.exists():
        if not source_settings.exists():
            raise FileNotFoundError(
                f"OpenHands settings not found at {source_settings}; run openhands once to initialise it."
            )
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_settings, target_settings)
        source_mcp = source_dir / "mcp.json"
        if source_mcp.exists():
            shutil.copy2(source_mcp, target_dir / "mcp.json")

    profile = json.loads(target_settings.read_text(encoding="utf-8"))
    profile.setdefault("llm", {})["reasoning_effort"] = reasoning_effort
    condenser = profile.get("condenser")
    if isinstance(condenser, dict):
        condenser_llm = condenser.get("llm")
        if isinstance(condenser_llm, dict):
            condenser_llm["reasoning_effort"] = reasoning_effort
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target_dir, delete=False
    ) as temporary:
        json.dump(profile, temporary, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, target_settings)
    return target_dir


def codex_output_schema() -> dict[str, object]:
    """Return a Codex-compatible strict JSON schema for ``WorkerResult``."""

    schema = WorkerResult.model_json_schema()

    def make_objects_strict(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" or "properties" in value:
                value["additionalProperties"] = False
                # Codex/OpenAI strict structured output requires every
                # declared property to appear in `required`, including fields
                # that Pydantic models as optional/defaulted. The model can
                # still return empty values for those fields; the contract is
                # simply explicit and therefore accepted by the API.
                properties = value.get("properties")
                if isinstance(properties, dict):
                    value["required"] = list(properties)
            for child in value.values():
                make_objects_strict(child)
        elif isinstance(value, list):
            for child in value:
                make_objects_strict(child)

    make_objects_strict(schema)
    return schema
