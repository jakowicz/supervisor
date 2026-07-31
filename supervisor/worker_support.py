"""Pure configuration helpers shared by coding-worker adapters."""

from __future__ import annotations

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
