"""Bridge OpenHands headless JSONL events to the WorkerResult contract."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from supervisor.models import NextStep, Task
from supervisor.worker_support import openhands_base_url

from worker_adapter import emit, failure, parse_worker_result, repository_root, run_command, safety_gate, task_prompt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(Path(arguments.task_file).read_text(encoding="utf-8"))
    blocked = safety_gate("OpenHands", NextStep.USE_CODEX)
    if blocked:
        emit(blocked)
        return
    native_base_url = openhands_base_url(os.getenv("LLM_MODEL", ""), os.getenv("LLM_BASE_URL"))
    if native_base_url:
        os.environ["LLM_BASE_URL"] = native_base_url
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as prompt_file:
        prompt_file.write(task_prompt(task))
        prompt_path = Path(prompt_file.name)
    try:
        command = [
            "openhands", "--headless", "--json", "--llm-approve",
            "--override-with-envs", "--file", str(prompt_path),
        ]
        code, stdout, stderr = run_command(command, repository_root())
    finally:
        prompt_path.unlink(missing_ok=True)
    if code != 0:
        emit(failure("OpenHands", f"exited with code {code}", NextStep.USE_CODEX, stdout, stderr))
        return
    result = parse_worker_result(stdout)
    if result:
        result.evidence.agent_log = stdout + stderr
    emit(result or failure("OpenHands", "did not emit a valid WorkerResult JSON object", NextStep.USE_CODEX, stdout, stderr))


if __name__ == "__main__":
    main()
