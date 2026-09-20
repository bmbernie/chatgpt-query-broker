from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class WorkerRole(StrEnum):
    PLANNER = "planner"
    SPECIALIST = "specialist"
    REVIEWER = "reviewer"
    ADJUDICATOR = "adjudicator"


class WorkerState(StrEnum):
    SLEEPING = "sleeping"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    RECOVERING = "recovering"
    FAILED = "failed"
    DELETED = "deleted"


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INDETERMINATE = "indeterminate"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Worker:
    worker_id: str
    role: WorkerRole
    model: str
    reasoning_level: str
    state: WorkerState
    provider_session_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    worker_id: str
    state: OperationState
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
