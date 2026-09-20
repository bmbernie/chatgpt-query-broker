from __future__ import annotations

import asyncio
from typing import Protocol

from .models import Worker, WorkerState
from .provider import (
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)
from .store import BrokerStore


class ProviderProtocol(Protocol):
    async def create_session(
        self,
        session_id: str,
        model: str,
        level: str,
    ) -> ProviderSession: ...

    async def get_session(
        self,
        session_id: str,
    ) -> ProviderSession: ...

    async def list_sessions(
        self,
    ) -> list[ProviderSession]: ...

    async def delete_session(
        self,
        session_id: str,
    ) -> None: ...


class WorkerLifecycleError(RuntimeError):
    pass


class ProviderSessionMismatch(WorkerLifecycleError):
    pass


class BrokerService:
    def __init__(
        self,
        store: BrokerStore,
        provider: ProviderProtocol,
    ):
        self.store = store
        self.provider = provider

        self._locks_guard = asyncio.Lock()
        self._worker_locks: dict[str, asyncio.Lock] = {}

    async def _worker_lock(
        self,
        worker_id: str,
    ) -> asyncio.Lock:
        async with self._locks_guard:
            return self._worker_locks.setdefault(
                worker_id,
                asyncio.Lock(),
            )

    @staticmethod
    def _validate_session(
        worker: Worker,
        session: ProviderSession,
    ) -> None:
        if (
            session.model != worker.model
            or session.level != worker.reasoning_level
        ):
            raise ProviderSessionMismatch(
                "provider session configuration mismatch: "
                f"worker={worker.worker_id} "
                f"expected_model={worker.model} "
                f"actual_model={session.model} "
                f"expected_level={worker.reasoning_level} "
                f"actual_level={session.level}"
            )

    async def wake_worker(
        self,
        worker_id: str,
    ) -> Worker:
        lock = await self._worker_lock(worker_id)

        async with lock:
            worker = self.store.get_worker(worker_id)

            if worker is None:
                raise KeyError(worker_id)

            # READY is only trusted if the provider still agrees.
            if (
                worker.state == WorkerState.READY
                and worker.provider_session_id
            ):
                try:
                    session = await self.provider.get_session(
                        worker.provider_session_id
                    )
                except ProviderSessionNotFound:
                    worker = (
                        self.store.clear_worker_provider_session(
                            worker_id,
                            WorkerState.SLEEPING,
                        )
                    )
                else:
                    self._validate_session(worker, session)
                    return worker

            if worker.state == WorkerState.BUSY:
                raise WorkerLifecycleError(
                    f"worker is busy: {worker_id}"
                )

            self.store.transition_worker(
                worker_id,
                WorkerState.STARTING,
            )

            session_id = worker_id

            try:
                try:
                    session = await self.provider.create_session(
                        session_id,
                        worker.model,
                        worker.reasoning_level,
                    )
                except ProviderSessionConflict:
                    session = await self.provider.get_session(
                        session_id
                    )

                self._validate_session(worker, session)

            except Exception:
                # Preserve any known binding for diagnosis.
                self.store.transition_worker(
                    worker_id,
                    WorkerState.FAILED,
                )
                raise

            return self.store.transition_worker(
                worker_id,
                WorkerState.READY,
                provider_session_id=session.session_id,
            )

    async def sleep_worker(
        self,
        worker_id: str,
    ) -> Worker:
        lock = await self._worker_lock(worker_id)

        async with lock:
            worker = self.store.get_worker(worker_id)

            if worker is None:
                raise KeyError(worker_id)

            if worker.state == WorkerState.BUSY:
                raise WorkerLifecycleError(
                    f"cannot sleep busy worker: {worker_id}"
                )

            session_id = worker.provider_session_id

            if session_id:
                try:
                    await self.provider.delete_session(
                        session_id
                    )
                except ProviderSessionNotFound:
                    pass

            return self.store.clear_worker_provider_session(
                worker_id,
                WorkerState.SLEEPING,
            )

    async def reconcile_after_restart(
        self,
    ) -> dict[str, int]:
        recovery = self.store.recover_after_restart()

        provider_sessions = {
            session.session_id: session
            for session in await self.provider.list_sessions()
        }

        ready = 0
        sleeping = 0
        failed = 0

        for worker in self.store.list_workers():
            if worker.state != WorkerState.RECOVERING:
                continue

            # Deterministic provider IDs allow recovery from a crash
            # after provider creation but before binding persistence.
            session_id = (
                worker.provider_session_id
                or worker.worker_id
            )

            session = provider_sessions.get(session_id)

            if session is None:
                self.store.clear_worker_provider_session(
                    worker.worker_id,
                    WorkerState.SLEEPING,
                )
                sleeping += 1
                continue

            try:
                self._validate_session(worker, session)
            except ProviderSessionMismatch:
                self.store.transition_worker(
                    worker.worker_id,
                    WorkerState.FAILED,
                    provider_session_id=session_id,
                )
                failed += 1
                raise

            self.store.transition_worker(
                worker.worker_id,
                WorkerState.READY,
                provider_session_id=session_id,
            )
            ready += 1

        return {
            **recovery,
            "workers_ready": ready,
            "workers_sleeping": sleeping,
            "workers_failed": failed,
        }
