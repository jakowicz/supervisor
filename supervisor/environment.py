"""Project-root environment loading for a reusable Supervisor checkout."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def project_root_for(package_root: Path) -> Path:
    """Return the controlled project root above its ``supervisor/`` checkout."""

    return package_root.resolve().parent


def load_project_environment(
    package_root: Path, *, env_file: Path | None = None, override: bool = False
) -> Path:
    """Load a project ``.env`` without depending on the shell cwd."""

    default = project_root_for(package_root) / ".env"
    path = (env_file or Path(os.getenv("SUPERVISOR_ENV_FILE", default))).expanduser().resolve()
    load_dotenv(path, override=override)
    load_dotenv(path.with_name(".secrets.env"), override=True)
    return path.parent


def project_path(value: str | os.PathLike[str], root: Path) -> Path:
    """Resolve a configured path relative to the project root.

    Older configurations used ``..`` / ``../.state`` while living inside the
    submodule. Preserve that exact convention after a safe move to project root.
    """

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    raw = str(candidate)
    if raw == "..":
        return root.resolve()
    if raw.startswith("../.state/"):
        return (root / raw.removeprefix("../")).resolve()
    return (root / candidate).resolve()
