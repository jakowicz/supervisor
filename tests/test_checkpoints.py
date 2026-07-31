import json
from pathlib import Path
from types import SimpleNamespace

from supervisor.checkpoints import continuation_brief, diff_snapshot, stream_checkpoint, stream_delta


def test_stream_checkpoint_extracts_qwen_session_and_last_tool(tmp_path: Path):
    log = tmp_path / "qwen.log"
    log.write_text(
        "\n".join(
            [
                '[stdout] ' + json.dumps({"type": "system", "subtype": "init", "session_id": "session-42", "model": "qwen"}),
                '[stdout] ' + json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "run_shell_command"}]}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    checkpoint, offset = stream_checkpoint(log)
    assert offset > 0
    assert checkpoint["session_id"] == "session-42"
    assert checkpoint["next_action"] == "continue_from_last_agent_tool"
    assert "run_shell_command" in checkpoint["summary"]


def test_continuation_brief_is_small_and_actionable():
    brief = continuation_brief(
        {
            "status": "interrupted",
            "changed_files_json": '["lib/time.dart"]',
            "continuation_summary": "Agent last requested: run_shell_command.",
            "next_action": "continue_from_last_agent_tool",
        }
    )
    assert "do not reimplement completed work" in brief
    assert "lib/time.dart" in brief
    assert "continue_from_last_agent_tool" in brief


def test_stream_delta_only_returns_new_output(tmp_path: Path):
    log = tmp_path / "qwen.log"
    log.write_text("first\n", encoding="utf-8")
    first, offset = stream_delta(log)
    log.write_text("first\nsecond\n", encoding="utf-8")
    second, final_offset = stream_delta(log, offset)
    assert first == "first\n"
    assert second == "second\n"
    assert final_offset > offset


def test_diff_snapshot_is_scoped_to_the_configured_project_directory(monkeypatch, tmp_path: Path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=" M app.js\n")

    monkeypatch.setattr("supervisor.checkpoints.subprocess.run", fake_run)

    snapshot = diff_snapshot(tmp_path)

    assert commands == [
        ["git", "status", "--short", "--", "."],
        ["git", "diff", "--binary", "--", "."],
    ]
    assert snapshot["changed_files"] == ["app.js"]
