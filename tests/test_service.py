import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from chatgpt_worker_broker.models import (
    WorkerRole,
    WorkerState,
)
from chatgpt_worker_broker.service import (
    BrokerService,
    ProviderSessionMismatch,
)
from chatgpt_worker_broker.store import BrokerStore
from chatgpt_worker_broker.provider import (
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)


def make_store(tmp_path: Path) -> BrokerStore:
    return BrokerStore(tmp_path / "broker.sqlite3")


@dataclass
class FakeProvider:
    sessions: dict[str, ProviderSession]
    create_calls: int = 0
    delete_calls: int = 0
    create_delay: float = 0.0

    def __init__(self):
        self.sessions = {}
        self.create_calls = 0
        self.delete_calls = 0
        self.create_delay = 0.0

    async def create_session(
        self,
        session_id: str,
        model: str,
        level: str,
        conversation_policy: str = "regular",
    ) -> ProviderSession:
        self.create_calls += 1

        if self.create_delay:
            await asyncio.sleep(self.create_delay)

        if session_id in self.sessions:
            raise ProviderSessionConflict(
                f"session already exists: {session_id}"
            )

        session = ProviderSession(
            session_id=session_id,
            model=model,
            level=level,
            conversation_policy=conversation_policy,
            state="ready",
        )
        self.sessions[session_id] = session
        return session

    async def get_session(
        self,
        session_id: str,
    ) -> ProviderSession:
        try:
            return self.sessions[session_id]
        except KeyError:
            raise ProviderSessionNotFound(session_id) from None

    async def list_sessions(self) -> list[ProviderSession]:
        return [
            self.sessions[key]
            for key in sorted(self.sessions)
        ]

    async def delete_session(
        self,
        session_id: str,
    ) -> None:
        self.delete_calls += 1

        if session_id not in self.sessions:
            raise ProviderSessionNotFound(session_id)

        del self.sessions[session_id]


def create_worker(store, worker_id="re-high", level="high"):
    return store.create_worker(
        worker_id=worker_id,
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level=level,
    )


def test_wake_worker_creates_provider_session_and_becomes_ready(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        service = BrokerService(store, provider)

        create_worker(store)

        worker = await service.wake_worker("re-high")

        assert worker.state == WorkerState.READY
        assert worker.provider_session_id == "re-high"

        assert provider.create_calls == 1

        session = provider.sessions["re-high"]
        assert session.model == "chatgpt-5.6-sol-web"
        assert session.level == "high"

    asyncio.run(run())


def test_concurrent_wake_only_creates_one_provider_session(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        provider.create_delay = 0.05

        service = BrokerService(store, provider)
        create_worker(store)

        first, second = await asyncio.gather(
            service.wake_worker("re-high"),
            service.wake_worker("re-high"),
        )

        assert first.state == WorkerState.READY
        assert second.state == WorkerState.READY
        assert provider.create_calls == 1

    asyncio.run(run())


def test_sleep_worker_deletes_session_and_clears_binding(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()
        service = BrokerService(store, provider)

        create_worker(store)

        await service.wake_worker("re-high")
        worker = await service.sleep_worker("re-high")

        assert worker.state == WorkerState.SLEEPING
        assert worker.provider_session_id is None
        assert provider.delete_calls == 1
        assert "re-high" not in provider.sessions

    asyncio.run(run())


def test_recovery_reclaims_matching_provider_session(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()

        create_worker(store)

        store.transition_worker(
            "re-high",
            WorkerState.READY,
            provider_session_id="re-high",
        )

        provider.sessions["re-high"] = ProviderSession(
            session_id="re-high",
            model="chatgpt-5.6-sol-web",
            level="high",
            state="ready",
        )

        service = BrokerService(store, provider)

        result = await service.reconcile_after_restart()

        worker = store.get_worker("re-high")

        assert result["workers_ready"] == 1
        assert worker is not None
        assert worker.state == WorkerState.READY
        assert worker.provider_session_id == "re-high"

    asyncio.run(run())


def test_recovery_finds_session_created_before_binding_persisted(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()

        create_worker(store)

        # Broker crashed in STARTING after provider creation but before
        # provider_session_id was persisted.
        store.transition_worker(
            "re-high",
            WorkerState.STARTING,
        )

        provider.sessions["re-high"] = ProviderSession(
            session_id="re-high",
            model="chatgpt-5.6-sol-web",
            level="high",
            state="ready",
        )

        service = BrokerService(store, provider)

        result = await service.reconcile_after_restart()

        worker = store.get_worker("re-high")

        assert result["workers_ready"] == 1
        assert worker is not None
        assert worker.state == WorkerState.READY
        assert worker.provider_session_id == "re-high"

    asyncio.run(run())


def test_recovery_missing_provider_session_returns_worker_sleeping(
    tmp_path,
):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()

        create_worker(store)

        store.transition_worker(
            "re-high",
            WorkerState.READY,
            provider_session_id="re-high",
        )

        service = BrokerService(store, provider)

        result = await service.reconcile_after_restart()

        worker = store.get_worker("re-high")

        assert result["workers_sleeping"] == 1
        assert worker is not None
        assert worker.state == WorkerState.SLEEPING
        assert worker.provider_session_id is None

    asyncio.run(run())


def test_recovery_rejects_mismatched_provider_session(tmp_path):
    async def run():
        store = make_store(tmp_path)
        provider = FakeProvider()

        create_worker(store)

        store.transition_worker(
            "re-high",
            WorkerState.READY,
            provider_session_id="re-high",
        )

        provider.sessions["re-high"] = ProviderSession(
            session_id="re-high",
            model="chatgpt-5.6-sol-web",
            level="xhigh",
            state="ready",
        )

        service = BrokerService(store, provider)

        with pytest.raises(ProviderSessionMismatch):
            await service.reconcile_after_restart()

        worker = store.get_worker("re-high")

        assert worker is not None
        assert worker.state == WorkerState.FAILED
        assert worker.provider_session_id == "re-high"

    asyncio.run(run())


def test_provider_session_policy_mismatch_is_rejected(tmp_path):
    from chatgpt_worker_broker.models import ConversationPolicy
    from chatgpt_worker_broker.service import ProviderSessionMismatch

    store = make_store(tmp_path)

    worker = store.create_worker(
        worker_id="isolated-high",
        role=WorkerRole.SPECIALIST,
        model="chatgpt-5.6-sol-web",
        reasoning_level="high",
        conversation_policy=(
            ConversationPolicy.TEMPORARY_UNPERSONALIZED
        ),
    )

    session = ProviderSession(
        session_id="isolated-high",
        model="chatgpt-5.6-sol-web",
        level="high",
        state="ready",
        conversation_policy="regular",
    )

    try:
        BrokerService._validate_session(
            worker,
            session,
        )
    except ProviderSessionMismatch:
        pass
    else:
        raise AssertionError(
            "provider policy mismatch was accepted"
        )
