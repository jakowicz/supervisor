"""Typed task and worker contracts shared by every supervisor node."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    PASS = "pass"
    REPAIRABLE_FAILURE = "repairable_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    ESCALATION_NEEDED = "escalation_needed"
    NEEDS_USER_REVIEW = "needs_user_review"


class NextStep(str, Enum):
    COMPLETE = "complete"
    RETRY_QWEN = "retry_qwen"
    USE_OPENHANDS = "use_openhands"
    USE_CODEX = "use_codex"
    ASK_USER = "ask_user"


class Evidence(BaseModel):
    # Raw process output is preserved for both successful and failed workers.
    # The structured fields below remain convenient summaries for reports.
    agent_log: str = ""
    adapter_log: str = ""
    test_log: str = ""
    browser_log: str = ""
    screenshots: list[str] = Field(default_factory=list)


class CriterionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_VERIFIED = "not_verified"


class AcceptanceResult(BaseModel):
    criterion: str
    status: CriterionStatus
    evidence: str


class DocumentationReport(BaseModel):
    reviewed_files: list[str] = Field(default_factory=list)
    updated_files: list[str] = Field(default_factory=list)
    summary: str = ""


class WorkerResult(BaseModel):
    """The required response shape for every worker, including adapters."""

    status: Status
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    test_result: str = ""
    evidence: Evidence = Field(default_factory=Evidence)
    acceptance_results: list[AcceptanceResult] = Field(default_factory=list)
    documentation: DocumentationReport = Field(default_factory=DocumentationReport)
    known_limitations: list[str] = Field(default_factory=list)
    browser_coverage: str = ""
    recommended_next_step: NextStep


class Task(BaseModel):
    task_id: str
    title: str
    objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_level: str = "normal"
    sequence: int = 0
    browser_impact: str = "not_applicable"
    playwright_specs: list[str] = Field(default_factory=list)
    # The graph sets this only for the independent Codex final-review stage.
    # Runbooks always remain implementation contracts and need not declare it.
    execution_mode: str = "implementation"
    # Runtime-only continuation packet assembled from durable checkpoints.
    # Runbooks do not need to declare it.
    continuation_context: str = ""


class RunEvent(BaseModel):
    """One immutable supervisor transition, suitable for audit and dashboards."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str
    agent: str
    model: str
    attempt: int
    status: Status
    summary: str
    route: str
    result: WorkerResult


class TaskRun(BaseModel):
    run_id: str
    task: Task
    status: Status
    route: str
    worker_results: list[WorkerResult]
    events: list[RunEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: list[str] = Field(default_factory=list)


def model_to_dict(value: BaseModel) -> dict[str, Any]:
    """Use JSON mode so enum and timestamp values can be stored directly."""

    return value.model_dump(mode="json")
