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
    criteria = [line[2:].strip() for line in _section(document, "Acceptance criteria").splitlines() if line.startswith("- ")]
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
    )
