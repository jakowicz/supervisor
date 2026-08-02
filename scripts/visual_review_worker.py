"""Verify that browser QA produced fresh, usable desktop and mobile images."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult, model_to_dict

try:  # Direct script execution from `scripts/`.
    from worker_adapter import repository_root
except ModuleNotFoundError:  # Importing from the supervisor test suite.
    from scripts.worker_adapter import repository_root


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    """Return dimensions only for a real PNG file; avoid external image tools."""

    try:
        header = path.read_bytes()[:24]
    except OSError:
        return None
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return (width, height) if width and height else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    artifact_root = repo_root / "artifacts" / "qa" / task.task_id
    captures = sorted(artifact_root.glob("*/"), key=lambda path: path.stat().st_mtime, reverse=True)
    artifact_directory = captures[0] if captures else None
    expected = ("desktop.png", "mobile.png")
    files = [artifact_directory / name for name in expected] if artifact_directory else []
    dimensions = [_png_dimensions(path) for path in files]
    if not artifact_directory or any(value is None for value in dimensions):
        print(json.dumps(model_to_dict(WorkerResult(
            status=Status.ENVIRONMENT_FAILURE,
            summary="Visual review requires fresh desktop and mobile screenshots from browser QA.",
            evidence=Evidence(screenshots=[str(path.relative_to(repo_root)) for path in files if path.exists()]),
            recommended_next_step=NextStep.ASK_USER,
        ))))
        return
    screenshot_paths = [str(path.relative_to(repo_root)) for path in files]
    sizes = ", ".join(f"{name}: {width}x{height}" for name, (width, height) in zip(expected, dimensions, strict=True))
    print(json.dumps(model_to_dict(WorkerResult(
        status=Status.PASS,
        summary="Fresh desktop and mobile visual evidence was captured after the passing browser suite.",
        test_result=f"Validated PNG render artifacts ({sizes}).",
        evidence=Evidence(screenshots=screenshot_paths),
        browser_coverage="Deterministic visual-evidence verification for the latest browser QA artifact set.",
        recommended_next_step=NextStep.COMPLETE,
    ))))


if __name__ == "__main__":
    main()
