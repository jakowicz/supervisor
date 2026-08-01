import json

from supervisor.models import Task
from supervisor.worker_support import codex_output_schema, openhands_base_url

from scripts.worker_adapter import parse_worker_result, task_prompt


def test_parser_reads_result_embedded_in_json_event():
    output = '{"event":"complete","message":"{\\"status\\":\\"pass\\",\\"summary\\":\\"done\\",\\"recommended_next_step\\":\\"complete\\"}"}'
    result = parse_worker_result(output)
    assert result is not None
    assert result.status.value == "pass"
    assert result.summary == "done"


def test_parser_returns_none_for_non_contract_output():
    assert parse_worker_result('{"event":"progress"}') is None


def test_parser_reads_result_from_jsonl_event_stream():
    output = '\n'.join([
        '{"event":"agent_started"}',
        '{"event":"final","content":{"status":"needs_user_review","summary":"scope changed","recommended_next_step":"ask_user"}}',
    ])
    result = parse_worker_result(output)
    assert result is not None
    assert result.status.value == "needs_user_review"


def test_parser_normalises_qwen_double_encoded_schema_fields():
    result_payload = {
        "status": "pass",
        "summary": "done",
        "acceptance_results": json.dumps([{"criterion": "works", "status": "pass", "evidence": "test"}]),
        "documentation": json.dumps({"files": []}),
        "recommended_next_step": "complete",
    }
    output = json.dumps({"type": "result", "result": json.dumps(result_payload)})
    result = parse_worker_result(output)
    assert result is not None
    assert result.status.value == "pass"
    assert result.acceptance_results[0].criterion == "works"
    assert result.documentation.reviewed_files == []


def test_codex_schema_makes_every_object_definition_strict():
    schema = codex_output_schema()

    def assert_strict(value):
        if isinstance(value, dict):
            if value.get("type") == "object" or "properties" in value:
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value.get("properties", {}))
            for child in value.values():
                assert_strict(child)
        elif isinstance(value, list):
            for child in value:
                assert_strict(child)

    assert_strict(schema)


def test_openhands_uses_native_ollama_root_for_ollama_provider():
    assert openhands_base_url(
        "ollama/qwen3-coder-next:latest", "http://127.0.0.1:11434/v1/"
    ) == "http://127.0.0.1:11434"


def test_openhands_leaves_non_ollama_openai_endpoint_unchanged():
    assert openhands_base_url("openai/gpt-5", "https://example.test/v1") == "https://example.test/v1"


def test_final_codex_prompt_requires_requirements_review_and_repair():
    prompt = task_prompt(
        Task(task_id="D006", title="Time", execution_mode="final_verification")
    )

    assert "mandatory final verifier/fixer" in prompt
    assert "Treat the current worktree as the candidate\nsolution" in prompt
    assert "retry this same Codex final-review stage" in prompt
    assert "Do\nnot run `flutter`, `dart`" in prompt
    assert "independent test stage" in prompt
    assert "Do not return `needs_user_review` solely because Flutter/Dart" in prompt


def test_static_browser_mode_tells_coders_to_leave_browser_evidence_to_qa(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_BROWSER_QA_MODE", "static")

    prompt = task_prompt(Task(task_id="M001", title="Mock", execution_mode="final_verification"))

    assert "Do not install or attempt to launch Playwright" in prompt
    assert "authoritative browser-contract\ncheck" in prompt
