from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from .query_backend import (
    QueryBackend,
    QueryBackendBusy,
    QueryBackendPolicyError,
    QueryBackendProtocolError,
    QueryBackendRequestError,
    QueryAttachment,
    QueryBackendUnavailable,
    QueryHandle,
    QueryInteractionNotFound,
    QueryRequest,
    ToolsPolicy,
)


class QueryAPIAttachment(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    kind: Literal[
        "image",
        "audio",
        "file",
    ]

    path: str = Field(
        min_length=1
    )


class QueryInteractionResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    result: dict[str, Any]


class QueryAPIRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    input: str = Field(
        min_length=1
    )

    cwd: str = Field(
        min_length=1
    )

    model: str = Field(
        min_length=1
    )

    reasoning_effort: str = Field(
        min_length=1
    )

    attachments: list[
        QueryAPIAttachment
    ] = Field(
        default_factory=list
    )

    # Kept as thread_id in the public HTTP API
    # for compatibility with existing q clients.
    thread_id: str | None = None

    backend: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )

    tools: ToolsPolicy | None = None

    ephemeral: bool = True

    sandbox: Literal[
        "read-only",
        "workspace-write",
        "danger-full-access",
    ] = "read-only"


# Python-level compatibility names. The HTTP API
# remains unchanged while the broker internals use
# backend-neutral terminology.
CodexInteractionResponse = (
    QueryInteractionResponse
)
CodexQueryRequest = QueryAPIRequest


def _line(
    value: dict[str, Any],
) -> bytes:
    return (
        json.dumps(
            value,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _request_error(
    exc: QueryBackendRequestError,
) -> HTTPException:
    # Preserve the existing q/broker wire contract.
    return HTTPException(
        status_code=502,
        detail={
            "error": "codex_request_error",
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        },
    )


def _unavailable(
    exc: QueryBackendUnavailable,
) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "codex_unavailable",
            "message": str(exc),
        },
    )


def _wire_event(
    event: dict[str, Any],
) -> dict[str, Any]:
    """Translate backend-neutral IDs to the stable q wire schema."""

    value = dict(event)

    conversation_id = value.pop(
        "conversation_id",
        None,
    )
    execution_id = value.pop(
        "execution_id",
        None,
    )

    if conversation_id is not None:
        value["thread_id"] = conversation_id

    if execution_id is not None:
        value["turn_id"] = execution_id

    # CodexQueryBackend reports both a generic
    # classification and the legacy Codex error.
    # Existing q clients expect the latter.
    if (
        value.get("type")
        == "transport_error"
        and "backend_error" in value
    ):
        value["error"] = value.pop(
            "backend_error"
        )
        value.pop(
            "backend",
            None,
        )

    return value


async def _stream_handle(
    handle: QueryHandle,
):
    thread_event: dict[str, Any] = {
        "type": "thread",
        "thread_id": handle.conversation_id,
    }

    if handle.backend is not None:
        thread_event["backend"] = (
            handle.backend
        )

    yield _line(thread_event)

    yield _line(
        {
            "type": "turn",
            "thread_id": (
                handle.conversation_id
            ),
            "turn_id": (
                handle.execution_id
            ),
        }
    )

    try:
        async for event in handle.events:
            yield _line(
                _wire_event(event)
            )

    finally:
        # Ensure provider-side subscriptions and
        # unanswered interactions are cleaned up
        # when an HTTP stream is abandoned early.
        close = getattr(
            handle.events,
            "aclose",
            None,
        )

        if close is not None:
            await close()


def create_query_router(
    backend: QueryBackend | None,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/interactions/"
        "{interaction_id}/respond"
    )
    async def respond_to_interaction(
        interaction_id: str,
        response: QueryInteractionResponse,
    ):
        if backend is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                },
            )

        try:
            receipt = (
                await backend.respond_interaction(
                    interaction_id,
                    response.result,
                )
            )

        except QueryInteractionNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": (
                        "interaction_not_found"
                    ),
                },
            ) from exc

        except QueryBackendPolicyError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": exc.error,
                    "message": exc.message,
                },
            ) from exc

        except QueryBackendUnavailable as exc:
            raise _unavailable(exc) from exc

        return {
            "responded": True,
            "interaction_id": (
                receipt.interaction_id
            ),
            "thread_id": (
                receipt.conversation_id
            ),
            "method": receipt.method,
        }

    @router.post(
        "/v1/threads/{thread_id}/"
        "turns/{turn_id}/interrupt"
    )
    async def interrupt_turn(
        thread_id: str,
        turn_id: str,
    ):
        if backend is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                },
            )

        try:
            await backend.interrupt(
                thread_id,
                turn_id,
            )

        except QueryBackendPolicyError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": exc.error,
                    "message": exc.message,
                },
            ) from exc

        except QueryBackendRequestError as exc:
            raise _request_error(
                exc
            ) from exc

        except QueryBackendUnavailable as exc:
            raise _unavailable(
                exc
            ) from exc

        return {
            "interrupted": True,
            "thread_id": thread_id,
            "turn_id": turn_id,
        }

    @router.post("/v1/query")
    async def query(
        req: QueryAPIRequest,
    ):
        if backend is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                },
            )

        request = QueryRequest(
            input=req.input,
            cwd=req.cwd,
            model=req.model,
            reasoning_effort=(
                req.reasoning_effort
            ),
            attachments=tuple(
                QueryAttachment(
                    kind=attachment.kind,
                    path=attachment.path,
                )
                for attachment
                in req.attachments
            ),
            conversation_id=req.thread_id,
            backend=req.backend,
            tools=req.tools,
            ephemeral=req.ephemeral,
            sandbox=req.sandbox,
        )

        try:
            handle = await backend.start_query(
                request
            )

        except QueryBackendPolicyError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": exc.error,
                    "message": exc.message,
                },
            ) from exc

        except QueryBackendBusy as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "thread_busy",
                    "thread_id": (
                        exc.conversation_id
                        or req.thread_id
                    ),
                    "message": str(exc),
                },
            ) from exc

        except QueryBackendProtocolError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": (
                        "invalid_codex_response"
                    ),
                    "message": str(exc),
                },
            ) from exc

        except QueryBackendRequestError as exc:
            raise _request_error(
                exc
            ) from exc

        except QueryBackendUnavailable as exc:
            raise _unavailable(
                exc
            ) from exc

        return StreamingResponse(
            _stream_handle(handle),
            media_type=(
                "application/x-ndjson"
            ),
            headers={
                # Retained for q compatibility.
                "X-Codex-Thread-Id": (
                    handle.conversation_id
                ),
                "X-Codex-Turn-Id": (
                    handle.execution_id
                ),
            },
        )

    return router
