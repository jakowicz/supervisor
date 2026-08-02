from pathlib import Path

from supervisor.environment import project_path


def test_project_path_keeps_moved_submodule_environment_paths_compatible(tmp_path: Path):
    assert project_path("..", tmp_path) == tmp_path
    assert project_path("../.state/supervisor.sqlite3", tmp_path) == tmp_path / ".state" / "supervisor.sqlite3"
    assert project_path(".state/supervisor.sqlite3", tmp_path) == tmp_path / ".state" / "supervisor.sqlite3"
