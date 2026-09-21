import asyncio
from dataclasses import dataclass
from pathlib import Path

from chatgpt_worker_broker.models import (
    OperationState,
    WorkerRole,
    WorkerState,
)
from chatgpt_worker_broker.provider import (
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)
from chatgpt_worker_broker.service import BrokerService
from chatgpt_worker_broker.store import BrokerStore


@dataclass(frozen=True)
class FakeCompletion:
    response_id: str
    session_id: str
    model: str
    level: str
    content: str


class FakeProvider:
    def __init__(self):
        self.sessions = {}
        self.complete_calls = 0
        self.complete_delay = 0.0
        self.fail_completion = False

        self.active_global = 0
        self.max_active_global = 0

        self.active_by_session = {}
        self.max_active_by_session = {}

    async def create_session(
        self,
        session_id,
        model,
        level,
        conversation_policy="regular",
    ):
        if session_id in self.sessions:
            raise ProviderSessionConflict(session_id)

        session = ProviderSession(
            session_id=session_id,
            model=model,
            level=level,
            conversation_policy=conversation_policy,
            state="ready",
        )

        self.sessions[session_id] = session
        return session

    async def get_session(self, session_id):
        try:
            return self.sessions[session_id]
        except KeyError:
            raise ProviderSessionNotFound(session_id) from None

    async def list_sessions(self):
        return list(self.sessions.values())

    async def delete_session(self, session_id):
        if session_id not in self.sessions:
            raise ProviderSessionNotFound(session_id)

        del self.sessions[session_id]

    async def complete_session(
        self,
        session_id,
        messages,
    ):
        self.complete_calls += 1

        self.active_global += 1
        self.max_active_global = max(
            self.max_active_global,
            self.active_global,
        )

        current = self.active_by_session.get(session_id, 0) + 1
        self.active_by_session[session_id] = current

        self.max_active_by_session[session_id] = max(
            self.max_active_by_session.get(session_id, 0),
            current,
        )

        try:
            if self.complete_delay:
                await asyncio.sleep(self.complete_delay)

            if self.fail_completion:
                raise RuntimeError("ambiguous provider failure")

            content = messages[-1]["content"]

            return FakeCompletion(
                response_id=f"response-{self.complete_calls}",
                session_id=session_id,
                model=self.sessions[session_id].model,
                level=self.sessions[session_id].level,
                content=f"completed:{content}",
            )

        finally:
            self.active_global -= 1
            self.active_by_session[session_id] -= 1


def make_store(tmp_path: Path):
    return BrokerStore(tmp_path / "broker.sqlite3")


def create_worker(
    store,
    worker_id="re-high",
    level="high",
):
    return store.create_worker(
        worker_id=worker_id,
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level=level,
    )


def request(content="work"):
    return {
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ]
    }


def test_operation_executes_once_and_persists_result(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        service = BrokerService(store, provider)

        create_worker(store)

        operation = await service.execute_operation(
            "op-001",
            "re-high",
            request("analyze"),
        )

        assert operation.state == OperationState.COMPLETED
        assert operation.result == {
            "response_id": "response-1",
            "content": "completed:analyze",
            "model": "chatgpt-5.6-sol-web",
            "level": "high",
        }

        assert provider.complete_calls == 1

        worker = store.get_worker("re-high")
        assert worker.state == WorkerState.READY

    asyncio.run(run())


def test_duplicate_operation_does_not_resubmit(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        service = BrokerService(store, provider)

        create_worker(store)

        first = await service.execute_operation(
            "op-001",
            "re-high",
            request("analyze"),
        )

        second = await service.execute_operation(
            "op-001",
            "re-high",
            request("analyze"),
        )

        assert first.state == OperationState.COMPLETED
        assert second.state == OperationState.COMPLETED
        assert first.result == second.result
        assert provider.complete_calls == 1

    asyncio.run(run())


def test_concurrent_duplicate_operation_submits_once(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        provider.complete_delay = 0.05

        service = BrokerService(store, provider)

        create_worker(store)

        first, second = await asyncio.gather(
            service.execute_operation(
                "op-001",
                "re-high",
                request("same"),
            ),
            service.execute_operation(
                "op-001",
                "re-high",
                request("same"),
            ),
        )

        assert first.state == OperationState.COMPLETED
        assert second.state == OperationState.COMPLETED
        assert provider.complete_calls == 1

    asyncio.run(run())


def test_different_operations_same_worker_serialize(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        provider.complete_delay = 0.05

        service = BrokerService(store, provider)

        create_worker(store)

        await asyncio.gather(
            service.execute_operation(
                "op-001",
                "re-high",
                request("one"),
            ),
            service.execute_operation(
                "op-002",
                "re-high",
                request("two"),
            ),
        )

        assert (
            provider.max_active_by_session["re-high"]
            == 1
        )

    asyncio.run(run())


def test_different_workers_can_execute_concurrently(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        provider.complete_delay = 0.05

        service = BrokerService(store, provider)

        create_worker(store, "re-high", "high")
        create_worker(store, "review-xhigh", "xhigh")

        await asyncio.gather(
            service.execute_operation(
                "op-high",
                "re-high",
                request("high"),
            ),
            service.execute_operation(
                "op-xhigh",
                "review-xhigh",
                request("xhigh"),
            ),
        )

        assert provider.max_active_global == 2

    asyncio.run(run())


def test_ambiguous_provider_failure_becomes_indeterminate(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        provider.fail_completion = True

        service = BrokerService(store, provider)

        create_worker(store)

        operation = await service.execute_operation(
            "op-001",
            "re-high",
            request("work"),
        )

        assert (
            operation.state
            == OperationState.INDETERMINATE
        )

        assert operation.error is not None

        worker = store.get_worker("re-high")
        assert worker.state == WorkerState.RECOVERING

        # Retrying the same operation ID must never submit it again.
        again = await service.execute_operation(
            "op-001",
            "re-high",
            request("work"),
        )

        assert again.state == OperationState.INDETERMINATE
        assert provider.complete_calls == 1

    asyncio.run(run())


def test_completed_operation_survives_service_restart_without_replay(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()

        create_worker(store)

        service_a = BrokerService(store, provider)

        completed = await service_a.execute_operation(
            "op-001",
            "re-high",
            request("persist"),
        )

        assert completed.state == OperationState.COMPLETED
        assert provider.complete_calls == 1

        # New service object, same durable store.
        service_b = BrokerService(
            BrokerStore(store.path),
            provider,
        )

        recovered = await service_b.execute_operation(
            "op-001",
            "re-high",
            request("persist"),
        )

        assert recovered.state == OperationState.COMPLETED
        assert recovered.result == completed.result
        assert provider.complete_calls == 1

    asyncio.run(run())


def test_wake_failure_marks_operation_failed_before_submission(
    tmp_path,
):
    import asyncio

    import pytest

    from chatgpt_worker_broker.models import (
        OperationState,
    )
    from chatgpt_worker_broker.provider import (
        ProviderRateLimitError,
    )
    from chatgpt_worker_broker.service import (
        BrokerService,
    )

    class RateLimitedProvider:
        async def create_session(
            self,
            *args,
            **kwargs,
        ):
            raise ProviderRateLimitError()

    async def run():
        store = make_store(tmp_path)
        create_worker(store)

        service = BrokerService(
            store,
            RateLimitedProvider(),
        )

        with pytest.raises(
            ProviderRateLimitError
        ):
            await service.execute_operation(
                "op-rate-limited",
                "re-high",
                request("probe"),
            )

        operation = store.get_operation(
            "op-rate-limited"
        )

        assert operation is not None
        assert (
            operation.state
            == OperationState.FAILED
        )
        assert operation.started_at is None
        assert operation.completed_at is not None
        assert operation.result is None
        assert (
            "ProviderRateLimitError"
            in operation.error
        )

    asyncio.run(run())
