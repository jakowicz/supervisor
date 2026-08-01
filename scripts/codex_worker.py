"""Bridge `codex exec` to the supervisor's WorkerResult contract."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from supervisor.models import NextStep, Task
from supervisor.worker_support import codex_output_schema

from worker_adapter import emit, failure, parse_worker_result, repository_root, run_command, safety_gate, task_prompt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(open(arguments.task_file, encoding="utf-8").read())
    blocked = safety_gate("Codex worker", NextStep.ASK_USER)
    if blocked:
        emit(blocked)
        return
    repo_root = repository_root()
    with tempfile.TemporaryDirectory(prefix="supervisor-codex-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        schema_path = temporary_path / "worker-result.schema.json"
        output_path = temporary_path / "final-response.json"
        schema_path.write_text(json.dumps(codex_output_schema()), encoding="utf-8")
        command = [
            "codex", "exec", "-C", str(repo_root), "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "--output-schema", str(schema_path), "--output-last-message", str(output_path),
        ]
        if os.getenv("CODEX_MODEL"):
            command.extend(["--model", os.environ["CODEX_MODEL"]])
        command.append(task_prompt(task))
        code, stdout, stderr = run_command(command, repo_root)
        if code != 0:
            emit(failure("Codex worker", f"exited with code {code}", NextStep.ASK_USER, stdout, stderr))
            return
        output = output_path.read_text(encoding="utf-8") if output_path.exists() else stdout
        result = parse_worker_result(output)
        if result:
            result.evidence.agent_log = stdout + stderr
        emit(result or failure("Codex worker", "did not emit a valid WorkerResult JSON object", NextStep.ASK_USER, stdout, stderr))


if __name__ == "__main__":
    main()
