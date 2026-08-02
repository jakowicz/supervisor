"""Load one scoped Markdown runbook into the supervisor task contract."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Task


def _section(document: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", document, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"Runbook is missing a ## {heading} section.")
    return match.group(1).strip()


def load_task(path: Path) -> Task:
    """Parse the deliberately small, dependency-free runbook format."""

    document = path.read_text(encoding="utf-8")
    metadata_match = re.match(r"^---\n(.*?)\n---\n", document, re.DOTALL)
    if not metadata_match:
        raise ValueError(f"{path} must start with YAML-style metadata.")
    metadata: dict[str, str] = {}
    for line in metadata_match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid runbook metadata line: {line}")
        metadata[key.strip()] = value.strip()
    required = {"task_id", "sequence", "title", "browser_impact", "playwright_spec"}
    missing = required - metadata.keys()
    if missing:
        raise ValueError(f"{path} is missing metadata: {', '.join(sorted(missing))}")
    is_r_series = re.fullmatch(r"R\d+", metadata["task_id"]) is not None
    if is_r_series:
        provenance_metadata = {"source_specifications", "source_catalogue_ids", "authoring_batch", "factory_stages"}
        missing_provenance_metadata = provenance_metadata - metadata.keys()
        if missing_provenance_metadata:
            raise ValueError(
                f"{path} is an R-series runbook and must declare provenance metadata: "
                f"{', '.join(sorted(missing_provenance_metadata))}"
            )
        source_specifications = [value.strip() for value in metadata["source_specifications"].split(",") if value.strip()]
        source_catalogue_ids = [value.strip() for value in metadata["source_catalogue_ids"].split(",") if value.strip()]
        factory_stages = [value.strip() for value in metadata["factory_stages"].split(",") if value.strip()]
        if not source_specifications or not source_catalogue_ids or not factory_stages:
            raise ValueError(f"{path} has incomplete R-series provenance metadata.")
        if not re.fullmatch(r"B\d+", metadata["authoring_batch"]):
            raise ValueError(f"{path} has invalid authoring_batch; use an ID such as B0001.")
        if any(not re.fullmatch(r"F\d+", stage) for stage in factory_stages):
            raise ValueError(f"{path} has invalid factory_stages; use comma-separated F-series IDs.")
        asset_metadata = {"asset_impact", "asset_ids"}
        missing_asset_metadata = asset_metadata - metadata.keys()
        if missing_asset_metadata:
            raise ValueError(
                f"{path} is an R-series runbook and must declare asset metadata: "
                f"{', '.join(sorted(missing_asset_metadata))}"
            )
        if metadata["asset_impact"] not in {"required", "not_applicable"}:
            raise ValueError(f"{path} has invalid asset_impact; use required or not_applicable.")
        declared_assets = [asset_id.strip() for asset_id in metadata["asset_ids"].split(",") if asset_id.strip()]
        if metadata["asset_impact"] == "required" and not declared_assets:
            raise ValueError(f"{path} requires assets but has no asset_ids.")
        if metadata["asset_impact"] == "not_applicable" and declared_assets:
            raise ValueError(f"{path} declares asset_ids but asset_impact is not_applicable.")
        audio_metadata = {"audio_impact", "audio_ids", "audio_brief", "audio_duration_seconds", "audio_loop", "audio_style_version"}
        missing_audio_metadata = audio_metadata - metadata.keys()
        if missing_audio_metadata:
            raise ValueError(
                f"{path} is an R-series runbook and must declare audio metadata: "
                f"{', '.join(sorted(missing_audio_metadata))}"
            )
        if metadata["audio_impact"] not in {"required", "not_applicable"}:
            raise ValueError(f"{path} has invalid audio_impact; use required or not_applicable.")
        declared_audio = [audio_id.strip() for audio_id in metadata["audio_ids"].split(",") if audio_id.strip()]
        if metadata["audio_impact"] == "required":
            if not declared_audio or not metadata["audio_brief"].strip():
                raise ValueError(f"{path} requires audio but has no audio_ids or audio_brief.")
            try:
                duration = int(metadata["audio_duration_seconds"])
            except ValueError as error:
                raise ValueError(f"{path} has invalid audio_duration_seconds.") from error
            if duration < 1:
                raise ValueError(f"{path} requires audio_duration_seconds greater than zero.")
            if metadata["audio_loop"] not in {"required", "not_required"}:
                raise ValueError(f"{path} has invalid audio_loop; use required or not_required.")
        elif declared_audio or metadata["audio_brief"].strip() or metadata["audio_duration_seconds"].strip() not in {"", "0"} or metadata["audio_loop"] not in {"", "not_applicable"}:
            raise ValueError(f"{path} declares audio fields but audio_impact is not_applicable.")
    criteria: list[str] = []
    current: list[str] = []
    for line in _section(document, "Acceptance criteria").splitlines():
        if line.startswith("- "):
            if current:
                criteria.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.strip():
            # Markdown permits a hanging indent for a long bullet. Preserve
            # the entire criterion so it remains a precise worker contract.
            current.append(line.strip())
    if current:
        criteria.append(" ".join(current))
    if not criteria:
        raise ValueError(f"{path} has no acceptance criteria.")
    return Task(
        task_id=metadata["task_id"],
        title=metadata["title"],
        sequence=int(metadata["sequence"]),
        browser_impact=metadata["browser_impact"],
        playwright_specs=[metadata["playwright_spec"]] if metadata["playwright_spec"] else [],
        objective=_section(document, "Objective"),
        acceptance_criteria=criteria,
        asset_impact=metadata.get("asset_impact", "not_applicable"),
        asset_brief=metadata.get("asset_brief", ""),
        asset_ids=[asset_id.strip() for asset_id in metadata.get("asset_ids", "").split(",") if asset_id.strip()],
        visual_style_version=metadata.get("visual_style_version", ""),
        audio_impact=metadata.get("audio_impact", "not_applicable"),
        audio_brief=metadata.get("audio_brief", ""),
        audio_ids=[audio_id.strip() for audio_id in metadata.get("audio_ids", "").split(",") if audio_id.strip()],
        audio_duration_seconds=int(metadata.get("audio_duration_seconds", "0") or 0),
        audio_loop=metadata.get("audio_loop", "not_applicable"),
        audio_style_version=metadata.get("audio_style_version", ""),
        source_specifications=[value.strip() for value in metadata.get("source_specifications", "").split(",") if value.strip()],
        source_catalogue_ids=[value.strip() for value in metadata.get("source_catalogue_ids", "").split(",") if value.strip()],
        authoring_batch=metadata.get("authoring_batch", ""),
        factory_stages=[value.strip() for value in metadata.get("factory_stages", "").split(",") if value.strip()],
    )
