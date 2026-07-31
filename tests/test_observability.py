import json

from supervisor.models import Evidence, NextStep, Status, Task, WorkerResult
from supervisor.observability import SupervisorTelemetry


class _Span:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _Tracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name, attributes=None, **_kwargs):
        span = _Span(name, attributes)
        self.spans.append(span)
        return span


def test_agent_jsonl_becomes_generation_tool_and_result_observations():
    transcript = "\n".join(
        json.dumps(record)
        for record in (
            {"type": "user", "message": {"content": [{"type": "text", "text": "Implement D006"}]}},
            {
                "type": "assistant",
                "message": {
                    "model": "qwen3-coder-next:latest",
                    "content": [{"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"file_path": "lib/a.dart"}}],
                    "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                },
            },
            {"type": "result", "result": "done", "is_error": False, "num_turns": 1, "usage": {"total_tokens": 15}},
        )
    )
    tracer = _Tracer()
    telemetry = SupervisorTelemetry(tracer=tracer)
    stage = _Span("stage", {})
    result = WorkerResult(
        status=Status.PASS,
        summary="done",
        evidence=Evidence(agent_log=transcript),
        recommended_next_step=NextStep.COMPLETE,
    )

    telemetry.complete_stage(stage, result, "test")

    assert [span.name for span in tracer.spans] == [
        "agent.generation.001",
        "agent.tool.001.read_file",
        "agent.result",
    ]
    generation = tracer.spans[0]
    assert json.loads(generation.attributes["langfuse.observation.usage_details"]) == {
        "input": 12,
        "output": 3,
        "total": 15,
    }
    assert tracer.spans[1].attributes["langfuse.observation.type"] == "tool"
    assert json.loads(tracer.spans[2].attributes["langfuse.observation.output"])["result"] == "done"


def test_live_checkpoint_creates_a_flushable_event(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_OBSERVABILITY_PROJECT_LABEL", "pocket-list-mock")
    tracer = _Tracer()
    telemetry = SupervisorTelemetry(tracer=tracer)
    telemetry.live_checkpoint(
        Task(task_id="D006", title="Time"), "run-1", "qwen", "Qwen3 Coder", 1,
        {"summary": "Agent is reading the clock implementation.", "stream_excerpt": "tool output"},
    )
    assert tracer.spans[0].name == "supervisor.live.qwen.001"
    assert tracer.spans[0].attributes["langfuse.observation.type"] == "event"
    assert tracer.spans[0].attributes["langfuse.trace.tags"] == ["supervisor", "D006", "pocket-list-mock"]


def test_agent_jsonl_omits_null_stop_reason_from_telemetry_attributes():
    transcript = json.dumps({
        "type": "assistant",
        "message": {"model": "qwen", "content": [], "stop_reason": None},
    })
    tracer = _Tracer()
    telemetry = SupervisorTelemetry(tracer=tracer)

    telemetry.complete_stage(
        _Span("stage", {}),
        WorkerResult(
            status=Status.PASS,
            summary="done",
            evidence=Evidence(agent_log=transcript),
            recommended_next_step=NextStep.COMPLETE,
        ),
        "test",
    )

    assert "langfuse.observation.metadata.stop_reason" not in tracer.spans[0].attributes
