"""Bridge Qwen Code's native output to the supervisor's WorkerResult contract."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from supervisor.models import NextStep, Task, WorkerResult

from worker_adapter import emit, failure, parse_worker_result, repository_root, run_command, safety_gate, task_prompt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    arguments = parser.parse_args()
    task = Task.model_validate_json(open(arguments.task_file, encoding="utf-8").read())
    blocked = safety_gate("Qwen3 Coder", NextStep.USE_OPENHANDS)
    if blocked:
        emit(blocked)
        return
    # Qwen Code uses OpenAI-compatible environment names for custom providers.
    # Map the existing local-Ollama supervisor settings without storing a real
    # credential. Ollama accepts the placeholder key on its local /v1 endpoint.
    if os.getenv("LLM_BASE_URL"):
        os.environ.setdefault("OPENAI_BASE_URL", os.environ["LLM_BASE_URL"])
    if os.getenv("LLM_API_KEY"):
        os.environ.setdefault("OPENAI_API_KEY", os.environ["LLM_API_KEY"])
    # Qwen Code's stream watchdog defaults to four minutes.  A local 58 GB
    # model can legitimately take longer to begin a generation after a tool
    # result, so keep this aligned with the supervisor's visible idle limit.
    os.environ.setdefault(
        "QWEN_STREAM_IDLE_TIMEOUT_MS",
        str(int(os.getenv("SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS", "600")) * 1000),
    )
    # Keep the project's Codebase Memory MCP available to Qwen. It provides
    # repository-aware discovery that the implementation worker relies on.
    # Qwen otherwise returns a readable Markdown summary; JSON-schema mode
    # registers a final structured-output tool and makes the contract reliable.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(WorkerResult.model_json_schema(), handle)
        schema_path = handle.name
    try:
        command = [
            "qwen",
            "--sandbox",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--allowed-mcp-server-names",
            "codebase-memory-mcp",
            "--allowed-tools",
            "run_shell_command",
            "--json-schema",
            f"@{schema_path}",
            "--prompt",
            task_prompt(task),
        ]
        # A checkpointed session retains the agent's own tool history and is
        # substantially cheaper than asking a local model to rediscover a
        # partially completed task after an interruption.
        if os.getenv("SUPERVISOR_QWEN_RESUME_SESSION_ID"):
            command.extend(["--resume", os.environ["SUPERVISOR_QWEN_RESUME_SESSION_ID"]])
        if os.getenv("QWEN_MODEL"):
            command.extend(["--model", os.environ["QWEN_MODEL"]])
        idle_timeout = int(os.getenv("SUPERVISOR_QWEN_IDLE_TIMEOUT_SECONDS", "300"))
        code, stdout, stderr = run_command(command, repository_root(), idle_timeout_seconds=idle_timeout)
    finally:
        Path(schema_path).unlink(missing_ok=True)
    if code != 0:
        emit(failure("Qwen3 Coder", f"exited with code {code}", NextStep.USE_OPENHANDS, stdout, stderr))
        return
    result = parse_worker_result(stdout)
    if result:
        result.evidence.agent_log = stdout + stderr
    emit(result or failure("Qwen3 Coder", "did not emit a valid WorkerResult JSON object", NextStep.USE_OPENHANDS, stdout, stderr))


if __name__ == "__main__":
    main()
