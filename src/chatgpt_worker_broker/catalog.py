from __future__ import annotations

from dataclasses import dataclass

from .models import WorkerRole
from .store import BrokerStore


MODEL = "chatgpt-5.6-sol-web"


@dataclass(frozen=True, slots=True)
class WorkerTemplate:
    worker_id: str
    role: WorkerRole
    reasoning_level: str
    model: str = MODEL


DEFAULT_WORKERS = (
    WorkerTemplate(
        "assessment-planner-xhigh",
        WorkerRole.PLANNER,
        "xhigh",
    ),
    WorkerTemplate(
        "re-high",
        WorkerRole.SPECIALIST,
        "high",
    ),
    WorkerTemplate(
        "pwn-high",
        WorkerRole.SPECIALIST,
        "high",
    ),
    WorkerTemplate(
        "crypto-high",
        WorkerRole.SPECIALIST,
        "high",
    ),
    WorkerTemplate(
        "web-high",
        WorkerRole.SPECIALIST,
        "high",
    ),
    WorkerTemplate(
        "forensics-high",
        WorkerRole.SPECIALIST,
        "high",
    ),
    WorkerTemplate(
        "review-xhigh",
        WorkerRole.REVIEWER,
        "xhigh",
    ),
    WorkerTemplate(
        "adjudicator-xhigh",
        WorkerRole.ADJUDICATOR,
        "xhigh",
    ),
)


def seed_default_workers(
    store: BrokerStore,
) -> list[str]:
    created = []

    for template in DEFAULT_WORKERS:
        if store.get_worker(template.worker_id) is not None:
            continue

        store.create_worker(
            worker_id=template.worker_id,
            role=template.role,
            model=template.model,
            reasoning_level=template.reasoning_level,
        )

        created.append(template.worker_id)

    return created
