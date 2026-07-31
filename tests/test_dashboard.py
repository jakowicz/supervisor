import sqlite3

from supervisor.dashboard import choose_dashboard_port, is_kiln_ledger_listener, is_kiln_ledger_process, live_logs, metrics, read_live_log, render, render_logs, run_summaries, source_signature


def test_dashboard_metrics_handles_empty_database():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE task_runs (payload TEXT)")
    data = metrics(connection)
    assert data["runs"] == 0
    assert "Kiln Ledger" in render(data)
    assert run_summaries(connection) == []
    assert "Review raw worker logs" in render_logs()
    assert "href='/logs'" in render(data)


def test_dashboard_process_detection(monkeypatch):
    class Completed:
        returncode = 0
        stdout = "python emberhold-dashboard --port 8765"

    monkeypatch.setattr("supervisor.dashboard.subprocess.run", lambda *args, **kwargs: Completed())
    assert is_kiln_ledger_process(12345)


def test_dashboard_listener_detection(monkeypatch):
    class Response:
        def read(self, _size):
            return b"<title>Kiln Ledger</title>"
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    monkeypatch.setattr("supervisor.dashboard.urlopen", lambda *_args, **_kwargs: Response())
    assert is_kiln_ledger_listener(8765)


def test_dashboard_chooses_next_free_port_when_default_is_busy(monkeypatch):
    monkeypatch.setattr("supervisor.dashboard.port_is_available", lambda port: port == 8766)
    monkeypatch.setattr("supervisor.dashboard.is_kiln_ledger_listener", lambda _port: False)

    assert choose_dashboard_port(8765, explicitly_requested=False) == 8766


def test_live_log_reader_rejects_paths_outside_the_live_directory(tmp_path):
    database = tmp_path / "supervisor.sqlite3"
    live = tmp_path / "live"
    live.mkdir()
    (live / "d006-01-qwen-example.log").write_text("still working", encoding="utf-8")
    assert live_logs(database)[0]["name"] == "d006-01-qwen-example.log"
    assert read_live_log(database, "d006-01-qwen-example.log") == "still working"
    assert read_live_log(database, "../secret.log") is None


def test_source_signature_changes_when_dashboard_source_changes(tmp_path):
    source = tmp_path / "dashboard.py"
    source.write_text("first", encoding="utf-8")
    initial = source_signature(tmp_path)
    source.write_text("second version", encoding="utf-8")
    assert source_signature(tmp_path) != initial


def test_log_page_server_renders_selected_live_log(tmp_path):
    database = tmp_path / "supervisor.sqlite3"
    live = tmp_path / "live"
    live.mkdir()
    (live / "older.log").write_text("older output", encoding="utf-8")
    (live / "newer.log").write_text("newer output", encoding="utf-8")
    page = render_logs(live_logs(database), database, "older.log")
    assert "older output" in page
    assert "value='older.log' selected" in page
    assert "live-log-select" in page
