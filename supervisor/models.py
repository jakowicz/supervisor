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
    # R-series work may be authored independently, but never implemented
    # before its declared contracts have been accepted.
    dependencies: list[str] = Field(default_factory=list)
    # A factory stage can pause its own collection until a project-scoped child
    # collection has completed (for example game design before implementation
    # runbook authoring).
    prerequisite_collections: list[str] = Field(default_factory=list)
    browser_impact: str = "not_applicable"
    playwright_specs: list[str] = Field(default_factory=list)
    # Asset stages are opt-in.  Most engineering runbooks deliberately leave
    # these at their safe defaults and never contact a local image generator.
    asset_impact: str = "not_applicable"
    asset_brief: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    visual_style_version: str = ""
    # Audio is an independent optional production lane. Music and sound effects
    # are explicit product assets, never an implicit side-effect of coding.
    audio_impact: str = "not_applicable"
    audio_brief: str = ""
    audio_ids: list[str] = Field(default_factory=list)
    audio_duration_seconds: int = 0
    audio_loop: str = "not_applicable"
    audio_style_version: str = ""
    # R-series contracts retain a compact, machine-readable trail back to the
    # canonical product decision and bounded authoring batch that created them.
    source_specifications: list[str] = Field(default_factory=list)
    source_catalogue_ids: list[str] = Field(default_factory=list)
    authoring_batch: str = ""
    factory_stages: list[str] = Field(default_factory=list)
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
