from pathlib import Path

from supervisor.environment import execution_project_root, load_project_environment, project_path


def test_project_path_keeps_moved_submodule_environment_paths_compatible(tmp_path: Path):
    assert project_path("..", tmp_path) == tmp_path
    assert project_path("../.state/supervisor.sqlite3", tmp_path) == tmp_path / ".state" / "supervisor.sqlite3"
    assert project_path(".state/supervisor.sqlite3", tmp_path) == tmp_path / ".state" / "supervisor.sqlite3"


def test_project_environment_loads_private_secrets_after_versioned_config(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "supervisor"
    package_root.mkdir()
    (tmp_path / ".env").write_text("ART_STYLE_NAME=public-style\nLLM_API_KEY=must-not-win\n", encoding="utf-8")
    (tmp_path / ".secrets.env").write_text("LLM_API_KEY=private-key\n", encoding="utf-8")
    monkeypatch.delenv("ART_STYLE_NAME", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    assert load_project_environment(package_root) == tmp_path
    assert __import__("os").environ["ART_STYLE_NAME"] == "public-style"
    assert __import__("os").environ["LLM_API_KEY"] == "private-key"


def test_execution_project_root_prefers_the_nearest_project_checkout(tmp_path: Path):
    project = tmp_path / "product"
    checkout = project / "supervisor"
    checkout.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    (checkout / ".git").mkdir()
    (project / "runbooks").mkdir()

    assert execution_project_root(tmp_path / "global-supervisor", project / "runbooks") == project
