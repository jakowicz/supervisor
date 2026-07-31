"""Parse a WorkerResult from plain JSON, JSONL, or wrapped agent events."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Iterable

from .models import WorkerResult


def _json_values(output: str) -> Iterable[object]:
    try:
        yield json.loads(output)
    except JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except JSONDecodeError:
            continue
        yield value


def _normalised_worker_result(value: dict[str, object]) -> dict[str, object]:
    """Tolerate local CLIs serialising nested schema objects as JSON strings."""

    candidate = dict(value)
    for field in ("evidence", "acceptance_results", "documentation"):
        encoded = candidate.get(field)
        if not isinstance(encoded, str):
            continue
        try:
            candidate[field] = json.loads(encoded)
        except JSONDecodeError:
            continue
    documentation = candidate.get("documentation")
    if isinstance(documentation, dict) and not {"reviewed_files", "updated_files", "summary"} & documentation.keys():
        files = documentation.get("files", [])
        candidate["documentation"] = {
            "reviewed_files": files if isinstance(files, list) else [],
            "updated_files": [],
            "summary": json.dumps(documentation, sort_keys=True),
        }
    return candidate


def parse_worker_result(output: str) -> WorkerResult | None:
    """Find a WorkerResult even when a CLI wraps it in event JSON."""

    pending: list[object] = list(_json_values(output))
    while pending:
        value = pending.pop(0)
        if isinstance(value, str):
            pending.extend(_json_values(value))
            continue
        if isinstance(value, list):
            pending.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        try:
            return WorkerResult.model_validate(_normalised_worker_result(value))
        except ValueError:
            pending.extend(value.values())
    return None
