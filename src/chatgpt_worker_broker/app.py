from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ConversationPolicy,
    Operation,
    Worker,
    WorkerRole,
)
from .provider import ProviderRateLimitError
from .query_api import create_query_router
from .service import (
    BrokerService,
    ProviderSessionMismatch,
    WorkerLifecycleError,
)
from .store import (
    BrokerStore,
    IdempotencyConflict,
)


class WorkerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )
    role: WorkerRole
    model: str
    reasoning_level: str
    conversation_policy: ConversationPolicy = (
        ConversationPolicy.REGULAR
    )


class OperationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    messages: list[dict]


def _dt(value: datetime | None):
    return (
        value.isoformat()
        if value is not None
        else None
    )


def _worker(worker: Worker) -> dict:
    return {
        "worker_id": worker.worker_id,
        "role": worker.role.value,
        "model": worker.model,
        "reasoning_level": worker.reasoning_level,
        "conversation_policy": worker.conversation_policy.value,
        "state": worker.state.value,
        "provider_session_id": worker.provider_session_id,
        "created_at": _dt(worker.created_at),
        "updated_at": _dt(worker.updated_at),
    }


def _operation(operation: Operation) -> dict:
    return {
        "operation_id": operation.operation_id,
        "worker_id": operation.worker_id,
        "state": operation.state.value,
        "request": operation.request,
        "result": operation.result,
        "error": operation.error,
        "created_at": _dt(operation.created_at),
        "started_at": _dt(operation.started_at),
        "completed_at": _dt(operation.completed_at),
    }


def create_app(
    *,
    store: BrokerStore,
    service: BrokerService,
    codex=None,
    lifespan=None,
) -> FastAPI:
    app = FastAPI(
        title="ChatGPT Worker Broker",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(
        create_query_router(codex)
    )

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "workers": len(store.list_workers()),
        }

    @app.get("/v1/workers")
    async def list_workers():
        return {
            "object": "list",
            "data": [
                _worker(worker)
                for worker in store.list_workers()
            ],
        }

    @app.post(
        "/v1/workers",
        status_code=201,
    )
    async def create_worker(
        req: WorkerCreateRequest,
    ):
        if store.get_worker(req.worker_id) is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "worker_exists",
                    "worker_id": req.worker_id,
                },
            )

        try:
            worker = store.create_worker(
                worker_id=req.worker_id,
                role=req.role,
                model=req.model,
                reasoning_level=req.reasoning_level,
                conversation_policy=req.conversation_policy,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "worker_exists",
                    "worker_id": req.worker_id,
                },
            ) from exc

        return _worker(worker)

    @app.get("/v1/workers/{worker_id}")
    async def get_worker(worker_id: str):
        worker = store.get_worker(worker_id)

        if worker is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "worker_not_found",
                    "worker_id": worker_id,
                },
            )

        return _worker(worker)

    @app.post("/v1/workers/{worker_id}/wake")
    async def wake_worker(worker_id: str):
        try:
            worker = await service.wake_worker(
                worker_id
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "worker_not_found",
                    "worker_id": worker_id,
                },
            ) from exc
        except ProviderRateLimitError as exc:
            headers = (
                {"Retry-After": exc.retry_after}
                if exc.retry_after
                else None
            )

            raise HTTPException(
                status_code=429,
                detail={
                    "error": "provider_rate_limited",
                    "message": str(exc),
                },
                headers=headers,
            ) from exc
        except ProviderSessionMismatch as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "provider_session_mismatch",
                    "message": str(exc),
                },
            ) from exc
        except WorkerLifecycleError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "worker_lifecycle_error",
                    "message": str(exc),
                },
            ) from exc

        return _worker(worker)

    @app.post("/v1/workers/{worker_id}/sleep")
    async def sleep_worker(worker_id: str):
        try:
            worker = await service.sleep_worker(
                worker_id
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "worker_not_found",
                    "worker_id": worker_id,
                },
            ) from exc
        except WorkerLifecycleError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "worker_lifecycle_error",
                    "message": str(exc),
                },
            ) from exc

        return _worker(worker)

    @app.post(
        "/v1/workers/{worker_id}/operations"
    )
    async def execute_operation(
        worker_id: str,
        req: OperationCreateRequest,
    ):
        request = {
            "messages": req.messages,
        }

        try:
            operation = await service.execute_operation(
                operation_id=req.operation_id,
                worker_id=worker_id,
                request=request,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "worker_not_found",
                    "worker_id": worker_id,
                },
            ) from exc
        except ProviderRateLimitError as exc:
            headers = (
                {"Retry-After": exc.retry_after}
                if exc.retry_after
                else None
            )

            raise HTTPException(
                status_code=429,
                detail={
                    "error": "provider_rate_limited",
                    "message": str(exc),
                },
                headers=headers,
            ) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_conflict",
                    "message": str(exc),
                },
            ) from exc

        return _operation(operation)

    @app.get("/v1/operations/{operation_id}")
    async def get_operation(operation_id: str):
        operation = store.get_operation(operation_id)

        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "operation_not_found",
                    "operation_id": operation_id,
                },
            )

        return _operation(operation)

    return app
