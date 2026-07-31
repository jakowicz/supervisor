"""LangGraph state. Nodes return partial updates to this shared contract."""

from __future__ import annotations

from typing import TypedDict

from .models import RunEvent, Task, WorkerResult


class SupervisorState(TypedDict, total=False):
    task: Task
    worker_results: list[WorkerResult]
    events: list[RunEvent]
    attempts: dict[str, int]
    active_agent: str
    route: str
    notes: list[str]
    final_status: str
    resume_stage: str
