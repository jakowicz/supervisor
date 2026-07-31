"""Local, read-only Kiln Ledger metrics dashboard for supervisor runs."""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

from .reports_cli import database_path


def dashboard_listener_pids(port: int) -> list[int]:
    """Return local listener PIDs. Failure is harmless and treated as unknown."""

    completed = subprocess.run(
        ["lsof", "-nP", "-t", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN"],
        capture_output=True, text=True, check=False,
    )
    return [int(value) for value in completed.stdout.split() if value.isdigit()]


def port_is_available(port: int) -> bool:
    """Check whether a localhost port can be reserved without starting a server."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_dashboard_port(preferred: int, *, explicitly_requested: bool) -> int:
    """Use the requested port or find a nearby free port for a new dashboard."""

    if explicitly_requested or port_is_available(preferred) or is_kiln_ledger_listener(preferred):
        return preferred
    for candidate in range(preferred + 1, preferred + 1_000):
        if port_is_available(candidate):
            return candidate
    raise RuntimeError(f"No free localhost dashboard port found near {preferred}.")


def is_kiln_ledger_process(pid: int) -> bool:
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    command = completed.stdout.lower()
    return "emberhold-dashboard" in command or "supervisor.dashboard" in command


def is_kiln_ledger_listener(port: int) -> bool:
    """Fallback verification for launchers whose process title hides Python args."""

    try:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
            page = response.read(16_384).decode("utf-8", errors="replace")
    except OSError:
        return False
    return "<title>Kiln Ledger</title>" in page


def stop_existing_dashboard(port: int, pid_path: Path) -> bool:
    """Stop only a verified older dashboard process so a restart is safe."""

    pids = dashboard_listener_pids(port)
    matching = [pid for pid in pids if is_kiln_ledger_process(pid)]
    try:
        recorded_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        recorded_pid = None
    if recorded_pid in pids:
        matching.append(recorded_pid)
    # Some Python launchers present a truncated command to `ps`. In that case,
    # verify the local HTTP page rather than guessing from the executable name.
    if not matching and is_kiln_ledger_listener(port):
        matching = pids
    if not matching or len(matching) != len(pids):
        return False
    for pid in matching:
        os.kill(pid, 15)
    return True


def metrics(connection: sqlite3.Connection) -> dict:
    rows = connection.execute("SELECT payload FROM task_runs ORDER BY rowid DESC").fetchall()
    runs = [json.loads(row[0]) for row in rows]
    events = [event for run in runs for event in run.get("events", [])]
    outcomes = Counter(run["status"] for run in runs)
    agents = Counter(event["agent"] for event in events)
    failures = Counter(event["status"] for event in events if event["status"] != "pass")
    return {
        "runs": len(runs),
        "accepted": outcomes["pass"],
        "review": outcomes["needs_user_review"],
        "attempts": len(events),
        "attempts_per_run": round(mean([len(run.get("events", [])) for run in runs]), 1) if runs else 0,
        "agents": agents,
        "failures": failures,
        "recent": runs[:12],
    }


def run_summaries(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("SELECT rowid AS ledger_rowid, payload FROM task_runs ORDER BY rowid DESC").fetchall()
    return [
        {
            "run_id": run.get("run_id", f"legacy-row-{row['ledger_rowid']}"),
            "task_id": run["task"]["task_id"],
            "title": run["task"]["title"],
            "status": run["status"],
            "mode": "dry-run" if any("Dry-run" in event.get("summary", "") for event in run.get("events", [])) else "real",
            "created_at": run["created_at"],
        }
        for row in rows
        for run in [json.loads(row["payload"])]
    ]


def full_run(connection: sqlite3.Connection, run_id: str) -> dict | None:
    for row in connection.execute("SELECT payload FROM task_runs ORDER BY rowid DESC"):
        run = json.loads(row[0])
        if run["run_id"] == run_id:
            return run
    return None


def live_logs(database: Path) -> list[dict]:
    directory = database.parent / "live"
    if not directory.exists():
        return []
    return [
        {"name": path.name, "modified_at": path.stat().st_mtime, "size": path.stat().st_size}
        for path in sorted(directory.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def read_live_log(database: Path, name: str) -> str | None:
    # A basename-only lookup prevents a local dashboard request escaping .state/live.
    if Path(name).name != name or not name.endswith(".log"):
        return None
    path = database.parent / "live" / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def source_signature(source_root: Path) -> tuple[tuple[str, int], ...]:
    """Cheap source fingerprint for the local dashboard auto-reloader."""

    return tuple(sorted(
        (str(path), path.stat().st_mtime_ns)
        for path in source_root.glob("*.py")
        if path.is_file()
    ))


LOG_REVIEW_PANEL = """
<section class='live-review' aria-labelledby='live-review-heading'>
  <div class='section-heading'><div><p class='eyebrow'>Before SQLite</p><h2 id='live-review-heading'>Live supervisor activity</h2></div><small id='live-status'>__LIVE_STATUS__</small></div>
  <div class='live-controls'><label>Live log<select id='live-log-select'>__LIVE_OPTIONS__</select></label></div>
  <pre id='live-log-output'>__LIVE_CONTENT__</pre>
</section>
<section class='log-review' aria-labelledby='log-review-heading'>
  <div class='section-heading'><div><p class='eyebrow'>Completed archive</p><h2 id='log-review-heading'>Review raw worker logs</h2></div><small id='log-status'>Loading completed runs…</small></div>
  <div class='log-controls'>
    <label>Task<select id='task-select'><option>Loading…</option></select></label>
    <label>Run<select id='run-select' disabled><option>Select a task</option></select></label>
    <label>Stage<select id='stage-select' disabled><option>Select a run</option></select></label>
  </div>
  <div class='log-meta' id='log-meta'>Only finished runs appear here. For the current job, use Live supervisor activity above; it is written to SQLite only after completion.</div>
  <pre id='raw-log' tabindex='0'>No log selected.</pre>
</section>
<script>
(() => {
  const task = document.querySelector('#task-select'), run = document.querySelector('#run-select'), stage = document.querySelector('#stage-select');
  const status = document.querySelector('#log-status'), meta = document.querySelector('#log-meta'), output = document.querySelector('#raw-log');
  const liveSelect = document.querySelector('#live-log-select'), liveStatus = document.querySelector('#live-status'), liveOutput = document.querySelector('#live-log-output');
  let summaries = [], activeRun = null;
  const option = (value, label) => { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; };
  const reset = (element, label) => { element.replaceChildren(option('', label)); element.disabled = true; };
  const raw = (e) => [['Raw agent output', e.agent_log], ['Adapter output', e.adapter_log], ['Test output', e.test_log], ['Browser output', e.browser_log]].filter(([,v]) => v).map(([n,v]) => `===== ${n} =====\n${v}`).join('\\n\\n') || 'This stage produced no raw process output.';
  function chooseTask() {
    const selected = task.value; reset(run, 'Select a run'); reset(stage, 'Select a stage'); activeRun = null; output.textContent = 'No log selected.';
    const matching = summaries.filter(item => item.task_id === selected); matching.forEach(item => run.append(option(item.run_id, `${item.mode} · ${item.status} · ${item.created_at} · ${item.run_id.slice(0,8)}`))); run.disabled = !matching.length;
  }
  async function chooseRun() {
    reset(stage, 'Loading stages…'); output.textContent = 'Loading run evidence…';
    const response = await fetch(`/api/run/${encodeURIComponent(run.value)}`); if (!response.ok) throw new Error(`Run archive returned ${response.status}`); activeRun = await response.json();
    stage.replaceChildren(option('', 'Select a stage'));
    activeRun.events.forEach((event, index) => stage.append(option(String(index), `${event.stage} · ${event.agent} · ${event.status}`)));
    stage.disabled = false; meta.textContent = `${activeRun.task.task_id} · ${activeRun.status} · ${activeRun.events.length} recorded stages`; output.textContent = 'Select a stage to view its complete preserved output.';
  }
  function chooseStage() { if (stage.value === '') return; const event = activeRun.events[Number(stage.value)]; output.textContent = raw(event.result.evidence); output.focus(); }
  task.addEventListener('change', chooseTask); run.addEventListener('change', () => chooseRun().catch(error => { status.textContent = error.message; output.textContent = 'Could not load this run. Check that the dashboard server is still running.'; })); stage.addEventListener('change', chooseStage);
  async function refreshSelectedLiveLog() { if (!liveSelect.value) return; const response = await fetch(`/api/live/${encodeURIComponent(liveSelect.value)}`, {cache:'no-store'}); if (response.ok) liveOutput.textContent = await response.text(); }
  liveSelect.addEventListener('change', async () => { await refreshSelectedLiveLog(); liveOutput.focus(); });
  fetch(`/api/live?fresh=${Date.now()}`, {cache:'no-store'}).then(r => { if (!r.ok) throw new Error(`Live archive returned ${r.status}`); return r.json(); }).then(items => { liveSelect.replaceChildren(option('', 'Select a live log')); items.forEach(item => liveSelect.append(option(item.name, item.name))); liveSelect.disabled = !items.length; liveStatus.textContent = items.length ? `${items.length} local log files available` : 'No live logs yet'; if (items.length) { liveSelect.value = items[0].name; liveSelect.dispatchEvent(new Event('change')); } }).catch(error => { liveStatus.textContent = `Could not load live logs: ${error.message}`; });
  fetch(`/api/runs?fresh=${Date.now()}`, {cache:'no-store'}).then(r => { if (!r.ok) throw new Error(`Run archive returned ${r.status}`); return r.json(); }).then(items => { summaries = items; const ids = [...new Set(items.map(item => item.task_id))]; task.replaceChildren(option('', 'Select a task')); ids.forEach(id => task.append(option(id, id))); task.disabled = false; status.textContent = `${items.length} completed runs available`; }).catch(error => { status.textContent = `Could not load local run archive: ${error.message}`; task.replaceChildren(option('', 'Archive unavailable')); });
  setInterval(() => refreshSelectedLiveLog().catch(() => {}), 5000);
})();
</script>
"""


def navigation(active: str) -> str:
    links = (("/", "Ledger", "ledger"), ("/logs", "Evidence archive", "logs"))
    return "<nav aria-label='Dashboard'><span class='nav-mark'>KILN /</span>" + "".join(
        f"<a href='{href}' class='{'active' if key == active else ''}'>{label}</a>"
        for href, label, key in links
    ) + "</nav>"


def render(data: dict) -> str:
    total = max(data["runs"], 1)
    acceptance = round(data["accepted"] / total * 100)
    agent_rows = "".join(f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>" for name, count in data["agents"].most_common()) or "<tr><td colspan='2'>No attempts yet</td></tr>"
    recent_rows = "".join(
        f"<article><b>{html.escape(run['task']['task_id'])}</b><span>{html.escape(run['status'])}</span><p>{html.escape(run['task']['title'])}</p><small>{' → '.join(event['agent'] for event in run.get('events', [])) or 'No worker events'}</small></article>"
        for run in data["recent"]
    ) or "<p>No supervisor runs yet.</p>"
    nav = navigation("ledger")
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Kiln Ledger</title><style>
    :root{{--obsidian:#19171e;--slate:#27242e;--ash:#eee2d1;--cinder:#e85d2a;--teal:#5fb8ad;--smoke:#a7a0aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--obsidian);color:var(--ash);font:16px ui-monospace,SFMono-Regular,Menlo,monospace}}main{{max-width:1180px;margin:auto;padding:26px 24px 44px}}nav{{display:flex;align-items:center;gap:8px;border-bottom:1px solid #514a57;padding:0 0 14px;margin-bottom:34px}}.nav-mark{{font-size:12px;color:var(--teal);letter-spacing:.14em;margin-right:8px}}nav a{{color:var(--smoke);text-decoration:none;padding:7px 10px;border:1px solid transparent}}nav a:hover,nav a.active{{border-color:#56505d;color:var(--ash);background:#27242e}}header{{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid #514a57;padding-bottom:20px}}h1{{font:700 42px Georgia,serif;margin:0;letter-spacing:-1px}}header p{{color:var(--smoke);margin:0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0}}.card,article{{background:var(--slate);border:1px solid #45404c;padding:18px}}.value{{font:700 38px Georgia,serif;color:var(--cinder);display:block;margin-top:8px}}.label{{color:var(--smoke);font-size:12px;text-transform:uppercase;letter-spacing:.12em}}section{{margin-top:32px}}h2{{font:700 24px Georgia,serif}}.columns{{display:grid;grid-template-columns:1fr 2fr;gap:20px}}table{{width:100%;border-collapse:collapse}}td{{padding:10px;border-bottom:1px solid #45404c}}article{{margin-bottom:10px;border-left:4px solid var(--teal)}}article span{{float:right;color:var(--cinder)}}article p{{margin:8px 0}}small{{color:var(--smoke)}}@media(max-width:720px){{.grid,.columns{{grid-template-columns:1fr 1fr}}header{{display:block}}header p{{margin-top:10px}}}}@media(max-width:960px){{.columns{{grid-template-columns:1fr}}}}</style></head><body><main>{nav}<header><div><p>Emberhold / supervisor observability</p><h1>Kiln Ledger</h1></div><p>Read-only local metrics</p></header><div class='grid'><div class='card'><span class='label'>Task runs</span><span class='value'>{data['runs']}</span></div><div class='card'><span class='label'>Accepted</span><span class='value'>{data['accepted']}</span></div><div class='card'><span class='label'>Acceptance rate</span><span class='value'>{acceptance}%</span></div><div class='card'><span class='label'>Average stages/run</span><span class='value'>{data['attempts_per_run']}</span></div></div><div class='columns'><section><h2>Worker load</h2><table>{agent_rows}</table><h2>Non-pass events</h2><table>{''.join(f'<tr><td>{html.escape(name)}</td><td>{count}</td></tr>' for name,count in data['failures'].most_common()) or '<tr><td>None</td><td>0</td></tr>'}</table></section><section><h2>Recent routing traces</h2>{recent_rows}</section></div></main></body></html>"""


def render_logs(live_items: list[dict] | None = None, database: Path | None = None, selected_live: str = "") -> str:
    nav = navigation("logs")
    live_items = live_items or []
    options = ["<option value=''>No live logs available</option>"]
    content = "No live logs have been written yet."
    if live_items:
        names = {item["name"] for item in live_items}
        selected_live = selected_live if selected_live in names else live_items[0]["name"]
        options = [
            f"<option value='{html.escape(item['name'], quote=True)}'{' selected' if item['name'] == selected_live else ''}>{html.escape(item['name'])}</option>"
            for item in live_items
        ]
        content = read_live_log(database, selected_live) if database else "Select a live log."
        content = content or "The newest live log could not be read."
    panel = LOG_REVIEW_PANEL.replace("__LIVE_STATUS__", f"{len(live_items)} local log files available" if live_items else "No live logs yet")
    panel = panel.replace("__LIVE_OPTIONS__", "".join(options))
    panel = panel.replace("__LIVE_CONTENT__", html.escape(content))
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Evidence archive · Kiln Ledger</title><style>
    :root{{--obsidian:#19171e;--slate:#27242e;--ash:#eee2d1;--cinder:#e85d2a;--teal:#5fb8ad;--smoke:#a7a0aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--obsidian);color:var(--ash);font:16px ui-monospace,SFMono-Regular,Menlo,monospace}}main{{max-width:1400px;margin:auto;padding:26px 24px 44px}}nav{{display:flex;align-items:center;gap:8px;border-bottom:1px solid #514a57;padding:0 0 14px;margin-bottom:34px}}.nav-mark{{font-size:12px;color:var(--teal);letter-spacing:.14em;margin-right:8px}}nav a{{color:var(--smoke);text-decoration:none;padding:7px 10px;border:1px solid transparent}}nav a:hover,nav a.active{{border-color:#56505d;color:var(--ash);background:#27242e}}.live-review{{padding-top:4px}}.log-review{{margin-top:38px;padding-top:28px;border-top:1px solid #514a57}}.section-heading{{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid #514a57;padding-bottom:20px}}.eyebrow{{color:var(--teal);font-size:12px;letter-spacing:.12em;text-transform:uppercase;margin:0}}h2{{font:700 38px Georgia,serif;margin:4px 0;letter-spacing:-1px}}small,.log-meta{{color:var(--smoke)}}.log-controls,.live-controls{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0 16px}}label{{color:var(--smoke);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}select,button{{display:block;width:100%;margin-top:6px;padding:10px;background:#15131a;color:var(--ash);border:1px solid #56505d;font:14px ui-monospace,SFMono-Regular,Menlo,monospace}}button{{align-self:end;cursor:pointer;color:var(--teal)}}.log-meta{{margin:12px 0}}pre{{min-height:220px;max-height:65vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#111016;border:1px solid #45404c;border-left:4px solid var(--cinder);padding:16px;color:#e9dfd2;line-height:1.5}}@media(max-width:720px){{.log-controls,.live-controls{{grid-template-columns:1fr}}.section-heading{{display:block}}}}</style></head><body><main>{nav}{panel}</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the read-only Emberhold supervisor metrics dashboard.")
    parser.add_argument("--port", type=int, help="Dashboard port. Defaults to the configured port or the next free localhost port.")
    parser.add_argument("--no-reload", action="store_true", help="Disable automatic restart when dashboard Python files change.")
    arguments = parser.parse_args()
    database = database_path()
    if not database.exists():
        raise SystemExit(f"No supervisor database at {database}; run a task first.")

    try:
        configured_port = int(os.getenv("SUPERVISOR_DASHBOARD_PORT", "8765"))
    except ValueError:
        configured_port = 8765
    preferred_port = arguments.port if arguments.port is not None else configured_port
    try:
        port = choose_dashboard_port(preferred_port, explicitly_requested=arguments.port is not None)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    if port != preferred_port:
        print(f"Preferred dashboard port {preferred_port} is busy; using free port {port}.")
    pid_path = database.parent / f"dashboard-{port}.pid"

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                parsed_url = urlparse(self.path)
                path = parsed_url.path
                if path == "/api/runs":
                    body = json.dumps(run_summaries(connection)).encode()
                    content_type = "application/json; charset=utf-8"
                elif path == "/api/live":
                    body = json.dumps(live_logs(database)).encode()
                    content_type = "application/json; charset=utf-8"
                elif path.startswith("/api/live/"):
                    content = read_live_log(database, unquote(path.removeprefix("/api/live/")))
                    if content is None:
                        self.send_error(404, "Live log was not found")
                        return
                    body = content.encode()
                    content_type = "text/plain; charset=utf-8"
                elif path.startswith("/api/run/"):
                    run = full_run(connection, unquote(path.removeprefix("/api/run/")))
                    if run is None:
                        self.send_error(404, "Run was not found")
                        return
                    body = json.dumps(run).encode()
                    content_type = "application/json; charset=utf-8"
                elif path == "/":
                    body = render(metrics(connection)).encode()
                    content_type = "text/html; charset=utf-8"
                elif path == "/logs":
                    selected_live = parse_qs(parsed_url.query).get("live", [""])[0]
                    body = render_logs(live_logs(database), database, selected_live).encode()
                    content_type = "text/html; charset=utf-8"
                else:
                    self.send_error(404, "Not found")
                    return
            finally:
                connection.close()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, format, *args):
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError as error:
        if error.errno != 48 or not stop_existing_dashboard(port, pid_path):
            raise SystemExit(
                f"Port {port} is already in use by a process that is not a verified Kiln Ledger dashboard. "
                f"Choose another port or stop that process yourself."
            ) from error
        for _ in range(20):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit(f"The previous Kiln Ledger process did not release port {arguments.port}.")
        print(f"Restarted existing Kiln Ledger on port {port}.")
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    print(f"Kiln Ledger: http://127.0.0.1:{port}")
    reload_requested = threading.Event()
    watcher_stop = threading.Event()

    def watch_sources() -> None:
        source_root = Path(__file__).resolve().parent
        signature = source_signature(source_root)
        while not watcher_stop.wait(1):
            if source_signature(source_root) != signature:
                print("Dashboard source changed; reloading…", flush=True)
                reload_requested.set()
                server.shutdown()
                return

    if not arguments.no_reload:
        threading.Thread(target=watch_sources, name="dashboard-reloader", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKiln Ledger stopped.")
    finally:
        watcher_stop.set()
        server.server_close()
        try:
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass
    if reload_requested.is_set():
        os.execv(sys.executable, [sys.executable, *sys.argv])
