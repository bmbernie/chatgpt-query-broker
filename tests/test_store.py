from pathlib import Path

import pytest

from chatgpt_worker_broker.models import (
    OperationState,
    WorkerRole,
    WorkerState,
)
from chatgpt_worker_broker.store import (
    BrokerStore,
    IdempotencyConflict,
)


def make_store(tmp_path: Path) -> BrokerStore:
    return BrokerStore(tmp_path / "broker.sqlite3")


def test_worker_persists_across_store_instances(tmp_path):
    store = make_store(tmp_path)

    worker = store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    assert worker.worker_id == "re-high"
    assert worker.role == WorkerRole.SPECIALIST
    assert worker.state == WorkerState.SLEEPING
    assert worker.provider_session_id is None

    # Simulate broker restart by constructing a new store instance.
    reopened = make_store(tmp_path)

    loaded = reopened.get_worker("re-high")

    assert loaded is not None
    assert loaded.worker_id == "re-high"
    assert loaded.role == WorkerRole.SPECIALIST
    assert loaded.model == "chatgpt-5.6-sol-web"
    assert loaded.reasoning_level == "high"
    assert loaded.state == WorkerState.SLEEPING


def test_workers_can_be_listed_in_stable_order(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="review-xhigh",
        role=WorkerRole.REVIEWER,
        model="chatgpt-5.6-sol-web",
        reasoning_level="xhigh",
    )

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    assert [
        worker.worker_id
        for worker in store.list_workers()
    ] == [
        "re-high",
        "review-xhigh",
    ]


def test_operation_id_is_idempotent_for_same_request(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    request = {
        "messages": [
            {
                "role": "user",
                "content": "Analyze this function.",
            }
        ]
    }

    first, first_created = store.create_operation(
        operation_id="op-001",
        worker_id="re-high",
        request=request,
    )

    second, second_created = store.create_operation(
        operation_id="op-001",
        worker_id="re-high",
        request=request,
    )

    assert first_created is True
    assert second_created is False

    assert first.operation_id == second.operation_id
    assert first.worker_id == second.worker_id
    assert first.request == second.request
    assert first.state == OperationState.QUEUED

    assert len(store.list_operations()) == 1


def test_operation_id_rejects_different_request(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    store.create_operation(
        operation_id="op-001",
        worker_id="re-high",
        request={
            "messages": [
                {"role": "user", "content": "first"}
            ]
        },
    )

    with pytest.raises(IdempotencyConflict):
        store.create_operation(
            operation_id="op-001",
            worker_id="re-high",
            request={
                "messages": [
                    {"role": "user", "content": "different"}
                ]
            },
        )

    assert len(store.list_operations()) == 1


def test_operation_id_rejects_different_worker(tmp_path):
    store = make_store(tmp_path)

    for worker_id in ("re-high", "pwn-high"):
        store.create_worker(
            worker_id=worker_id,
            role=WorkerRole.SPECIALIST,
            model="chatgpt-5.6-sol-web",
            reasoning_level="high",
        )

    request = {
        "messages": [
            {"role": "user", "content": "same payload"}
        ]
    }

    store.create_operation(
        operation_id="op-001",
        worker_id="re-high",
        request=request,
    )

    with pytest.raises(IdempotencyConflict):
        store.create_operation(
            operation_id="op-001",
            worker_id="pwn-high",
            request=request,
        )


def test_operation_result_persists_across_restart(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="review-xhigh",
        role=WorkerRole.REVIEWER,
        model="chatgpt-5.6-sol-web",
        reasoning_level="xhigh",
    )

    store.create_operation(
        operation_id="review-001",
        worker_id="review-xhigh",
        request={
            "messages": [
                {"role": "user", "content": "review this"}
            ]
        },
    )

    store.mark_operation_running("review-001")

    store.complete_operation(
        "review-001",
        result={
            "content": "review complete",
        },
    )

    reopened = make_store(tmp_path)
    operation = reopened.get_operation("review-001")

    assert operation is not None
    assert operation.state == OperationState.COMPLETED
    assert operation.result == {
        "content": "review complete",
    }
    assert operation.started_at is not None
    assert operation.completed_at is not None


def test_worker_state_and_provider_session_persist(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    starting = store.transition_worker(
        "re-high",
        WorkerState.STARTING,
    )
    assert starting.state == WorkerState.STARTING

    ready = store.transition_worker(
        "re-high",
        WorkerState.READY,
        provider_session_id="re-high",
    )

    assert ready.state == WorkerState.READY
    assert ready.provider_session_id == "re-high"

    reopened = make_store(tmp_path)
    loaded = reopened.get_worker("re-high")

    assert loaded is not None
    assert loaded.state == WorkerState.READY
    assert loaded.provider_session_id == "re-high"


def test_recovery_marks_volatile_workers_recovering(tmp_path):
    store = make_store(tmp_path)

    states = {
        "starting": WorkerState.STARTING,
        "ready": WorkerState.READY,
        "busy": WorkerState.BUSY,
        "sleeping": WorkerState.SLEEPING,
        "failed": WorkerState.FAILED,
    }

    for worker_id, state in states.items():
        store.create_worker(
            worker_id=worker_id,
            role=WorkerRole.SPECIALIST,
            model="chatgpt-5.6-sol-web",
            reasoning_level="high",
        )

        if state != WorkerState.SLEEPING:
            store.transition_worker(
                worker_id,
                state,
                provider_session_id=(
                    f"session-{worker_id}"
                    if state in {
                        WorkerState.READY,
                        WorkerState.BUSY,
                    }
                    else None
                ),
            )

    result = store.recover_after_restart()

    assert result["workers_recovering"] == 3

    assert (
        store.get_worker("starting").state
        == WorkerState.RECOVERING
    )
    assert (
        store.get_worker("ready").state
        == WorkerState.RECOVERING
    )
    assert (
        store.get_worker("busy").state
        == WorkerState.RECOVERING
    )

    assert (
        store.get_worker("sleeping").state
        == WorkerState.SLEEPING
    )
    assert (
        store.get_worker("failed").state
        == WorkerState.FAILED
    )

    # Preserve the provider binding for later reconciliation.
    assert (
        store.get_worker("ready").provider_session_id
        == "session-ready"
    )


def test_recovery_marks_running_operations_indeterminate(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    for operation_id in ("queued", "running", "completed"):
        store.create_operation(
            operation_id=operation_id,
            worker_id="re-high",
            request={
                "messages": [
                    {
                        "role": "user",
                        "content": operation_id,
                    }
                ]
            },
        )

    store.mark_operation_running("running")

    store.mark_operation_running("completed")
    store.complete_operation(
        "completed",
        result={"content": "done"},
    )

    result = store.recover_after_restart()

    assert result["operations_indeterminate"] == 1

    assert (
        store.get_operation("queued").state
        == OperationState.QUEUED
    )
    assert (
        store.get_operation("running").state
        == OperationState.INDETERMINATE
    )
    assert (
        store.get_operation("completed").state
        == OperationState.COMPLETED
    )


def test_recovery_is_idempotent(tmp_path):
    store = make_store(tmp_path)

    store.create_worker(
        worker_id="re-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
    )

    store.transition_worker(
        "re-high",
        WorkerState.READY,
        provider_session_id="re-high",
    )

    store.create_operation(
        operation_id="op-running",
        worker_id="re-high",
        request={
            "messages": [
                {"role": "user", "content": "work"}
            ]
        },
    )
    store.mark_operation_running("op-running")

    first = store.recover_after_restart()
    second = store.recover_after_restart()

    assert first == {
        "workers_recovering": 1,
        "operations_indeterminate": 1,
    }

    assert second == {
        "workers_recovering": 0,
        "operations_indeterminate": 0,
    }


def test_legacy_worker_schema_migrates_conversation_policy(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    path = tmp_path / "legacy.sqlite3"
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE workers (
                worker_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                model TEXT NOT NULL,
                reasoning_level TEXT NOT NULL,
                state TEXT NOT NULL,
                provider_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                state TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(worker_id)
                    REFERENCES workers(worker_id)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO workers (
                worker_id,
                role,
                model,
                reasoning_level,
                state,
                provider_session_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                "legacy-high",
                "specialist",
                "chatgpt-5.6-sol-web",
                "high",
                "sleeping",
                now,
                now,
            ),
        )

    store = BrokerStore(path)
    worker = store.get_worker("legacy-high")

    assert worker is not None
    assert worker.conversation_policy.value == "regular"

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(workers)"
            )
        }

    assert "conversation_policy" in columns
