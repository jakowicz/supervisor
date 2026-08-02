"""Build evidence from a locally served Flutter web release using Playwright."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult
from worker_adapter import emit, failure, repository_root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_server(url: str) -> bool:
    for _ in range(30):
        try:
            with urlopen(url, timeout=1) as response:
                return response.status == 200
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    repo_root = repository_root()
    web_build = repo_root / "build" / "web"
    if not web_build.joinpath("index.html").exists():
        emit(failure("Browser QA", "build/web is missing; the Flutter build worker must pass first", NextStep.ASK_USER))
        return
    supervisor_root = Path(__file__).resolve().parents[1]
    browser_root = supervisor_root / "browser"
    if not browser_root.joinpath("node_modules", "@playwright", "test").exists():
        emit(failure("Browser QA", "Playwright is not installed; run npm install and npx playwright install chromium", NextStep.ASK_USER))
        return
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = repo_root / "artifacts" / "qa" / task.task_id / run_stamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "playwright-report.json"
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(web_build)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        if not wait_for_server(base_url):
            emit(failure("Browser QA", "local Flutter web server did not start", NextStep.ASK_USER))
            return
        environment = dict(os.environ)
        environment.update({"BASE_URL": base_url, "QA_ARTIFACT_DIR": str(artifact_dir), "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report_path)})
        smoke_directory = browser_root / "tests" / "smoke"
        requested_specs = [browser_root / spec for spec in task.playwright_specs]
        missing_specs = [str(spec) for spec in requested_specs if not spec.exists()]
        if task.browser_impact == "required" and not requested_specs:
            emit(failure("Browser QA", "browser-impacting task did not declare a task-specific Playwright spec", NextStep.ASK_USER))
            return
        if missing_specs:
            emit(failure("Browser QA", "declared Playwright specs are missing: " + ", ".join(missing_specs), NextStep.ASK_USER))
            return
        full_suite = task.sequence > 0 and task.sequence % 5 == 0
        targets = [] if full_suite else [str(smoke_directory), *[str(spec) for spec in requested_specs]]
        completed = subprocess.run(
            ["npx", "playwright", "test", "--config", str(browser_root / "playwright.config.cjs"), *targets],
            cwd=browser_root, env=environment, capture_output=True, text=True, timeout=120,
        )
        log = completed.stdout + completed.stderr
        if completed.returncode != 0:
            screenshots = [str(path.relative_to(repo_root)) for path in artifact_dir.glob("*.png")]
            emit(WorkerResult(status=Status.REPAIRABLE_FAILURE, summary="Playwright browser QA failed.", test_result="Playwright returned a failing exit code.", evidence=Evidence(browser_log=log, screenshots=screenshots), recommended_next_step=NextStep.RETRY_QWEN))
            return
        capture = subprocess.run(
            ["node", str(browser_root / "scripts" / "capture_visual_evidence.cjs"), base_url, str(artifact_dir)],
            cwd=browser_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        log += "\n===== Visual evidence capture =====\n" + capture.stdout + capture.stderr
        screenshots = [str(path.relative_to(repo_root)) for path in artifact_dir.glob("*.png")]
        if capture.returncode != 0:
            emit(WorkerResult(status=Status.ENVIRONMENT_FAILURE, summary="Browser QA could not capture desktop and mobile visual evidence.", test_result="Playwright functional checks passed, but visual evidence capture failed.", evidence=Evidence(browser_log=log, screenshots=screenshots), recommended_next_step=NextStep.ASK_USER))
            return
        suite_name = "full" if full_suite else "smoke + task-specific"
        emit(WorkerResult(status=Status.PASS, summary=f"Playwright {suite_name} suite passed at desktop and mobile viewports with no browser errors.", test_result=f"Playwright {suite_name} suite passed.", evidence=Evidence(browser_log=log, screenshots=screenshots), browser_coverage=f"{suite_name}; specs: {', '.join(task.playwright_specs) or 'smoke only'}", recommended_next_step=NextStep.COMPLETE))
    except (OSError, subprocess.TimeoutExpired) as error:
        emit(failure("Browser QA", f"could not execute Playwright: {error}", NextStep.ASK_USER))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
