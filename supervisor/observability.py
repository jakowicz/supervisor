"""Optional local OpenTelemetry export for Langfuse.

The local SQLite ledger remains the authoritative raw-evidence archive.  This
module exports compact, structured copies of runs and stages only when the
operator explicitly enables it.
"""

from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .models import Task, WorkerResult


TRACE_ATTRIBUTES = (
    "langfuse.session.id",
    "langfuse.trace.name",
    "langfuse.trace.tags",
    "langfuse.trace.metadata.task_id",
    "langfuse.trace.metadata.run_id",
    "langfuse.environment",
)


def _enabled() -> bool:
    return os.getenv("SUPERVISOR_OBSERVABILITY_ENABLED", "false").lower() == "true"


def _bounded(value: str) -> str:
    limit = int(os.getenv("SUPERVISOR_OBSERVABILITY_RAW_LOG_MAX_CHARS", "16000"))
    if limit < 0 or len(value) <= limit:
        return value
    return f"[truncated to final {limit} characters for Langfuse]\n{value[-limit:]}"


def _live_bounded(value: str) -> str:
    """Keep a real-time event readable without duplicating an entire transcript."""

    limit = int(os.getenv("SUPERVISOR_OBSERVABILITY_LIVE_LOG_MAX_CHARS", "12000"))
    return value if len(value) <= limit else f"[truncated to final {limit} characters]\n{value[-limit:]}"


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _result_payload(result: WorkerResult) -> dict[str, Any]:
    evidence = result.evidence
    return {
        "status": result.status.value,
        "summary": result.summary,
        "changed_files": result.changed_files,
        "test_result": result.test_result,
        "browser_coverage": result.browser_coverage,
        "known_limitations": result.known_limitations,
        "screenshots": evidence.screenshots,
        "logs": {
            "agent": _bounded(evidence.agent_log),
            "adapter": _bounded(evidence.adapter_log),
            "test": _bounded(evidence.test_log),
            "browser": _bounded(evidence.browser_log),
        },
    }


def _stream_records(output: str) -> Iterator[dict[str, Any]]:
    """Decode Qwen-style JSONL without duplicating its partial stream events."""

    for line in output.splitlines():
        line = line.removeprefix("[stdout] ")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") != "stream_event":
            yield value


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _usage(record: dict[str, Any]) -> dict[str, int]:
    usage = record.get("usage") or record.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        return {}
    mapped = {
        "input": usage.get("input_tokens", usage.get("promptTokenCount")),
        "output": usage.get("output_tokens", usage.get("candidatesTokenCount")),
        "total": usage.get("total_tokens", usage.get("totalTokenCount")),
        "cache_read_input": usage.get("cache_read_input_tokens", usage.get("cachedContentTokenCount")),
        "reasoning": usage.get("thoughts_token_count", usage.get("thoughtsTokenCount")),
    }
    return {key: value for key, value in mapped.items() if isinstance(value, int)}


@dataclass
class SupervisorTelemetry:
    """Creates a trace tree without changing supervisor behaviour when off."""

    tracer: Any | None = None
    provider: Any | None = None
    root_span_kind: Any | None = None

    @classmethod
    def from_environment(cls) -> "SupervisorTelemetry":
        if not _enabled():
            return cls()
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        base_url = os.getenv("LANGFUSE_BASE_URL", "http://127.0.0.1:3001").rstrip("/")
        if not public_key or not secret_key:
            raise RuntimeError(
                "Observability is enabled but Langfuse credentials are missing. "
                "Create a project in local Langfuse and set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
            )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.trace import SpanKind
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{base_url}/api/public/otel/v1/traces",
            headers={
                "Authorization": f"Basic {auth}",
                "x-langfuse-ingestion-version": "4",
            },
        )
        provider = TracerProvider(resource=Resource.create({"service.name": "runbook-supervisor"}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        return cls(
            tracer=provider.get_tracer("runbook.supervisor"),
            provider=provider,
            root_span_kind=SpanKind.SERVER,
        )

    @property
    def is_enabled(self) -> bool:
        return self.tracer is not None

    def _common_attributes(self, task: Task, run_id: str) -> dict[str, Any]:
        project_label = os.getenv("SUPERVISOR_OBSERVABILITY_PROJECT_LABEL", "").strip()
        tags = ["supervisor", task.task_id]
        if project_label:
            tags.append(project_label)
        return {
            "langfuse.session.id": f"task:{task.task_id}",
            "langfuse.trace.name": "supervisor-run",
            "langfuse.trace.tags": tags,
            "langfuse.trace.metadata.task_id": task.task_id,
            "langfuse.trace.metadata.run_id": run_id,
            "langfuse.trace.metadata.project_label": project_label,
            "langfuse.environment": os.getenv("SUPERVISOR_OBSERVABILITY_ENVIRONMENT", "local"),
        }

    @contextmanager
    def run(self, task: Task, run_id: str, run_number: int) -> Iterator[Any]:
        if not self.tracer:
            yield None
            return
        attributes = self._common_attributes(task, run_id)
        attributes.update({
            "langfuse.observation.type": "span",
            "langfuse.observation.input": _json({
                "task_id": task.task_id,
                "title": task.title,
                "objective": task.objective,
                "acceptance_criteria": task.acceptance_criteria,
                "run_number": run_number,
            }),
        })
        # Langfuse treats an OpenTelemetry SERVER span as the application-root
        # observation. An INTERNAL root span is retained in raw events but does
        # not materialise a visible trace or session.
        options = {"attributes": attributes}
        if self.root_span_kind is not None:
            options["kind"] = self.root_span_kind
        with self.tracer.start_as_current_span("supervisor.run", **options) as span:
            yield span

    @contextmanager
    def stage(self, task: Task, run_id: str, stage: str, agent: str, model: str, attempt: int) -> Iterator[Any]:
        if not self.tracer:
            yield None
            return
        attributes = self._common_attributes(task, run_id)
        attributes.update({
            "langfuse.observation.type": "span",
            "langfuse.observation.input": _json({"stage": stage, "agent": agent, "model": model, "attempt": attempt}),
            "langfuse.observation.metadata.stage": stage,
            "langfuse.observation.metadata.agent": agent,
            "langfuse.observation.metadata.model": model,
            "langfuse.observation.metadata.attempt": attempt,
        })
        with self.tracer.start_as_current_span(f"supervisor.{stage}", attributes=attributes) as span:
            yield span

    def complete_stage(self, span: Any, result: WorkerResult, route: str) -> None:
        if span is None:
            return
        span.set_attribute("langfuse.observation.output", _json(_result_payload(result)))
        span.set_attribute("langfuse.observation.metadata.status", result.status.value)
        span.set_attribute("langfuse.observation.metadata.route", route)
        self._record_agent_activity(result.evidence.agent_log)

    def _record_agent_activity(self, agent_log: str) -> None:
        """Turn an agent's complete JSONL transcript into readable observations.

        The complete unmodified transcript remains on the enclosing stage span.
        These children make the Langfuse trace navigable without creating an
        observation for every partial streaming token.
        """

        if not self.tracer or not agent_log:
            return
        latest_input: Any = None
        generation_number = 0
        tool_number = 0
        pending_tools: dict[str, dict[str, Any]] = {}

        def record_tool(tool: dict[str, Any], output: Any = None, is_error: bool = False) -> None:
            nonlocal tool_number
            tool_number += 1
            attributes = {
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": _json(tool.get("input", {})),
                "langfuse.observation.metadata.tool_name": tool.get("name", "unknown"),
                "langfuse.observation.metadata.tool_use_id": tool.get("id", ""),
            }
            if output is not None:
                attributes["langfuse.observation.output"] = _json(output)
                attributes["langfuse.observation.metadata.is_error"] = is_error
            with self.tracer.start_as_current_span(
                f"agent.tool.{tool_number:03d}.{tool.get('name', 'unknown')}", attributes=attributes
            ):
                pass

        for record in _stream_records(agent_log):
            record_type = record.get("type")
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            if record_type == "user":
                latest_input = message.get("content", message.get("parts", []))
                for part in latest_input if isinstance(latest_input, list) else []:
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    tool = pending_tools.pop(part.get("tool_use_id", ""), {"name": "unknown", "id": part.get("tool_use_id", "")})
                    record_tool(tool, part.get("content"), bool(part.get("is_error")))
                continue
            if record_type == "assistant":
                generation_number += 1
                content = message.get("content", message.get("parts", []))
                attributes = {
                    "langfuse.observation.type": "generation",
                    "langfuse.observation.input": _json(latest_input),
                    "langfuse.observation.output": _json(content),
                    "langfuse.observation.metadata.model": message.get("model", ""),
                    "langfuse.observation.usage_details": _json(_usage(message)),
                }
                # OpenTelemetry attributes cannot be None. Several streaming
                # providers emit an explicit null stop_reason in intermediate
                # records; omit it rather than printing exporter warnings.
                stop_reason = message.get("stop_reason")
                if stop_reason is not None:
                    attributes["langfuse.observation.metadata.stop_reason"] = str(stop_reason)
                with self.tracer.start_as_current_span(
                    f"agent.generation.{generation_number:03d}", attributes=attributes
                ):
                    for part in content if isinstance(content, list) else []:
                        if not isinstance(part, dict) or part.get("type") != "tool_use":
                            continue
                        pending_tools[part.get("id", "")] = part
                continue
            if record_type == "result":
                for tool in pending_tools.values():
                    record_tool(tool)
                pending_tools.clear()
                with self.tracer.start_as_current_span(
                    "agent.result",
                    attributes={
                        "langfuse.observation.type": "event",
                        "langfuse.observation.output": _json({
                            "result": record.get("result", ""),
                            "is_error": record.get("is_error", False),
                            "duration_ms": record.get("duration_ms"),
                            "num_turns": record.get("num_turns"),
                            "usage": record.get("usage", {}),
                            "permission_denials": record.get("permission_denials", []),
                        }),
                    },
                ):
                    pass

    def complete_run(self, span: Any, status: str, route: str, event_count: int) -> None:
        if span is None:
            return
        span.set_attribute("langfuse.observation.output", _json({"status": status, "route": route, "event_count": event_count}))

    def live_checkpoint(
        self,
        task: Task,
        run_id: str,
        stage: str,
        agent: str,
        sequence: int,
        payload: dict[str, Any],
    ) -> None:
        """Export one completed, flushable event while a worker is still live.

        OpenTelemetry only exports a span after it ends. The normal run/stage
        spans deliberately remain open until their work finishes, so this
        short-lived child event provides genuine live visibility without
        compromising the final trace hierarchy.
        """

        if not self.tracer:
            return
        event_payload = dict(payload)
        excerpt = event_payload.pop("stream_excerpt", "")
        if excerpt:
            event_payload["stream_excerpt"] = _live_bounded(str(excerpt))
        attributes = self._common_attributes(task, run_id)
        attributes.update({
            "langfuse.observation.type": "event",
            "langfuse.observation.input": _json({"stage": stage, "agent": agent, "sequence": sequence}),
            "langfuse.observation.output": _json(event_payload),
            "langfuse.observation.metadata.stage": stage,
            "langfuse.observation.metadata.agent": agent,
            "langfuse.observation.metadata.live": True,
        })
        with self.tracer.start_as_current_span(
            f"supervisor.live.{stage}.{sequence:03d}", attributes=attributes
        ):
            pass
        # A worker may run for hours. Push this closed event now rather than
        # waiting for the enclosing run/span to end.
        if self.provider:
            self.provider.force_flush(timeout_millis=2_000)

    def flush(self) -> None:
        if self.provider:
            self.provider.force_flush(timeout_millis=10_000)
            self.provider.shutdown()
