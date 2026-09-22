from __future__ import annotations

import asyncio

from .models import (
    Operation,
    OperationState,
    Worker,
    WorkerState,
)
from .worker_provider import (
    ProviderCompletion,
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
    WorkerSessionProvider,
)
from .store import BrokerStore


ProviderProtocol = WorkerSessionProvider


class WorkerLifecycleError(RuntimeError):
    pass


class ProviderSessionMismatch(WorkerLifecycleError):
    pass


class BrokerService:
    def __init__(
        self,
        store: BrokerStore,
        provider: WorkerSessionProvider,
    ):
        self.store = store
        self.provider = provider

        self._locks_guard = asyncio.Lock()
        self._worker_locks: dict[str, asyncio.Lock] = {}

        # Duplicate callers for the same durable operation must rendezvous
        # before inspecting/executing it. This is separate from the worker
        # lock because different operation IDs on one worker still need
        # worker-level serialization.
        self._operation_locks_guard = asyncio.Lock()
        self._operation_locks: dict[str, asyncio.Lock] = {}

    async def _worker_lock(
        self,
        worker_id: str,
    ) -> asyncio.Lock:
        async with self._locks_guard:
            return self._worker_locks.setdefault(
                worker_id,
                asyncio.Lock(),
            )

    async def _operation_lock(
        self,
        operation_id: str,
    ) -> asyncio.Lock:
        async with self._operation_locks_guard:
            return self._operation_locks.setdefault(
                operation_id,
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
            or session.conversation_policy
            != worker.conversation_policy.value
        ):
            raise ProviderSessionMismatch(
                "provider session configuration mismatch: "
                f"worker={worker.worker_id} "
                f"expected_model={worker.model} "
                f"actual_model={session.model} "
                f"expected_level={worker.reasoning_level} "
                f"actual_level={session.level} "
                f"expected_policy="
                f"{worker.conversation_policy.value} "
                f"actual_policy={session.conversation_policy}"
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
                        worker.conversation_policy.value,
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

    async def execute_operation(
        self,
        operation_id: str,
        worker_id: str,
        request: dict,
    ) -> Operation:
        """Execute exactly one durable provider operation.

        operation_id is the idempotency boundary. Concurrent callers with
        the same ID rendezvous here; once an operation leaves QUEUED it is
        never automatically resubmitted.
        """
        messages = request.get("messages")

        if not isinstance(messages, list):
            raise ValueError(
                "operation request must contain a messages list"
            )

        # This transaction establishes durable idempotency and also checks
        # that reuse of an operation_id has identical worker/request data.
        self.store.create_operation(
            operation_id=operation_id,
            worker_id=worker_id,
            request=request,
        )

        operation_lock = await self._operation_lock(
            operation_id
        )

        async with operation_lock:
            # Re-read only after rendezvous. A concurrent invocation may
            # have completed while this caller waited for the operation
            # lock.
            operation = self.store.get_operation(
                operation_id
            )

            if operation is None:
                raise KeyError(operation_id)

            if operation.state != OperationState.QUEUED:
                return operation

            # wake_worker() owns the worker lock itself, so it must execute
            # before this method acquires that same lock.
            #
            # Failure here occurs before completion submission, so its
            # disposition is known: persist FAILED rather than leaving a
            # durable operation permanently QUEUED.
            try:
                await self.wake_worker(worker_id)
            except Exception as exc:
                self.store.fail_operation(
                    operation_id,
                    error=(
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                raise

            worker_lock = await self._worker_lock(
                worker_id
            )

            async with worker_lock:
                # A defensive second read keeps the durable record
                # authoritative.
                operation = self.store.get_operation(
                    operation_id
                )

                if operation is None:
                    raise KeyError(operation_id)

                if operation.state != OperationState.QUEUED:
                    return operation

                worker = self.store.get_worker(worker_id)

                if worker is None:
                    raise KeyError(worker_id)

                if (
                    worker.state != WorkerState.READY
                    or worker.provider_session_id is None
                ):
                    raise WorkerLifecycleError(
                        f"worker is not ready: {worker_id}"
                    )

                self.store.transition_worker(
                    worker_id,
                    WorkerState.BUSY,
                )

                self.store.mark_operation_running(
                    operation_id
                )

                try:
                    completion = (
                        await self.provider.complete_session(
                            worker.provider_session_id,
                            messages,
                        )
                    )

                except Exception as exc:
                    # Once submission may have reached the provider, its
                    # disposition is uncertain. Persist that uncertainty
                    # and never replay it automatically.
                    operation = (
                        self.store.mark_operation_indeterminate(
                            operation_id,
                            error=(
                                f"{type(exc).__name__}: {exc}"
                            ),
                        )
                    )

                    self.store.transition_worker(
                        worker_id,
                        WorkerState.RECOVERING,
                    )

                    return operation

                result = {
                    "response_id": completion.response_id,
                    "content": completion.content,
                    "model": completion.model,
                    "level": completion.level,
                }

                operation = self.store.complete_operation(
                    operation_id,
                    result=result,
                )

                self.store.transition_worker(
                    worker_id,
                    WorkerState.READY,
                )

                return operation

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
