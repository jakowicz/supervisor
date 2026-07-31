from pathlib import Path

from supervisor.recovery import latest_qwen_result, qwen_logs


def test_recovery_skips_incomplete_newer_log_for_latest_completed_result(tmp_path: Path):
    completed = tmp_path / "task-d006-run-01-stage-02-agent-qwen-complete.log"
    completed.write_text(
        '{"type":"result","result":"{\\"status\\":\\"pass\\",\\"summary\\":\\"done\\",\\"recommended_next_step\\":\\"complete\\"}"}',
        encoding="utf-8",
    )
    incomplete = tmp_path / "task-d006-run-02-stage-02-agent-qwen-incomplete.log"
    incomplete.write_text('{"type":"assistant"}', encoding="utf-8")
    recovered = latest_qwen_result(tmp_path, "D006")
    assert recovered is not None
    result, path = recovered
    assert result.status.value == "pass"
    assert path == completed
    assert qwen_logs(tmp_path, "D006")[0] == incomplete
