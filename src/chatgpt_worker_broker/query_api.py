from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from fastapi import (
    APIRouter,
    HTTPException,
)
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from .codex_models import (
    CodexNotification,
    CodexProcessExited,
    CodexRequestError,
    CodexServerRequest,
)


class ToolsPolicy(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class CodexInteractionResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    result: dict[str, Any]


@dataclass(
    frozen=True,
    slots=True,
)
class PendingInteraction:
    request_id: int | str
    thread_id: str
    method: str


class InteractionRegistry:
    def __init__(self):
        self._items: dict[
            str,
            PendingInteraction,
        ] = {}
        self._lock = threading.Lock()

    def register(
        self,
        *,
        request_id: int | str,
        thread_id: str,
        method: str,
    ) -> str:
        interaction_id = uuid.uuid4().hex

        pending = PendingInteraction(
            request_id=request_id,
            thread_id=thread_id,
            method=method,
        )

        with self._lock:
            self._items[
                interaction_id
            ] = pending

        return interaction_id

    def take(
        self,
        interaction_id: str,
    ) -> PendingInteraction | None:
        with self._lock:
            return self._items.pop(
                interaction_id,
                None,
            )


class CodexQueryRequest(BaseModel):
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

    thread_id: str | None = None

    tools: ToolsPolicy | None = None

    ephemeral: bool = True

    sandbox: Literal[
        "read-only",
        "workspace-write",
        "danger-full-access",
    ] = "read-only"


NO_TOOLS_CONFIG = {
    "features.apps": False,
    "features.code_mode": False,
    "features.code_mode_only": False,
    "features.context_management": False,
    "features.current_time_reminder": False,
    "features.deferred_executor": False,
    "features.enable_fanout": False,
    "features.goals": False,
    "features.hooks": False,
    "features.image_generation": False,
    "features.memories": False,
    "features.multi_agent": False,
    "features.multi_agent_v2": False,
    "features.plugins": False,
    "features.request_permissions_tool": False,
    "features.shell_snapshot": False,
    "features.shell_tool": False,
    "features.standalone_web_search": False,
    "features.token_budget": False,
    "features.tool_suggest": False,
    "features.unified_exec": False,
    "features.view_image": False,
    "orchestrator.skills.enabled": False,
    "skills.include_instructions": False,
    "token_budget.use_history_notes_extension": False,
    "tools.experimental_request_user_input.enabled": False,
    "tools.update_plan.enabled": False,
    "web_search": "disabled",
}


def _line(value: dict) -> bytes:
    return (
        json.dumps(
            value,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


async def _disabled_tools_config(
    codex,
    *,
    cwd: str,
) -> dict:
    result = await codex.request(
        "config/read",
        {
            "includeLayers": False,
            "cwd": cwd,
        },
    )

    config = (
        result.get("config", {})
        if isinstance(result, dict)
        else {}
    )

    mcp_servers = (
        config.get("mcp_servers", {})
        if isinstance(config, dict)
        else {}
    )

    disabled = dict(
        NO_TOOLS_CONFIG
    )

    disabled["mcp_servers"] = {
        name: {
            "enabled": False,
        }
        for name in mcp_servers
    }

    return disabled


def _request_error(
    exc: CodexRequestError,
) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={
            "error": "codex_request_error",
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        },
    )


async def _stream_turn(
    codex,
    *,
    thread_id: str,
    turn_id: str,
    interactions: InteractionRegistry,
):
    pending_interactions: set[str] = set()

    try:
        yield _line(
            {
                "type": "thread",
                "thread_id": thread_id,
            }
        )

        yield _line(
            {
                "type": "turn",
                "thread_id": thread_id,
                "turn_id": turn_id,
            }
        )

        while True:
            incoming = (
                await codex.next_thread_message(
                    thread_id
                )
            )

            if isinstance(
                incoming,
                CodexServerRequest,
            ):
                interaction_id = (
                    interactions.register(
                        request_id=(
                            incoming.request_id
                        ),
                        thread_id=thread_id,
                        method=incoming.method,
                    )
                )

                pending_interactions.add(
                    interaction_id
                )

                yield _line(
                    {
                        "type": "server_request",
                        "interaction_id": (
                            interaction_id
                        ),
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "method": (
                            incoming.method
                        ),
                        "params": (
                            incoming.params
                        ),
                    }
                )

                continue

            if not isinstance(
                incoming,
                CodexNotification,
            ):
                continue

            method = incoming.method
            params = incoming.params

            if (
                method
                == "item/agentMessage/delta"
                and params.get("turnId")
                == turn_id
            ):
                yield _line(
                    {
                        "type": "delta",
                        "thread_id": (
                            thread_id
                        ),
                        "turn_id": turn_id,
                        "item_id": (
                            params.get(
                                "itemId"
                            )
                        ),
                        "delta": (
                            params.get(
                                "delta",
                                "",
                            )
                        ),
                    }
                )

                continue

            if (
                method == "error"
                and params.get("turnId")
                == turn_id
            ):
                yield _line(
                    {
                        "type": "error",
                        "thread_id": (
                            thread_id
                        ),
                        "turn_id": turn_id,
                        "error": (
                            params.get(
                                "error"
                            )
                        ),
                        "will_retry": (
                            params.get(
                                "willRetry"
                            )
                        ),
                    }
                )

                continue

            if (
                method == "turn/completed"
                and (
                    params.get("turn")
                    or {}
                ).get("id")
                == turn_id
            ):
                turn = (
                    params.get("turn")
                    or {}
                )

                yield _line(
                    {
                        "type": "completed",
                        "thread_id": (
                            thread_id
                        ),
                        "turn_id": turn_id,
                        "status": (
                            turn.get(
                                "status"
                            )
                        ),
                        "error": (
                            turn.get(
                                "error"
                            )
                        ),
                    }
                )

                return

            yield _line(
                {
                    "type": "event",
                    "method": method,
                    "params": params,
                }
            )

    except CodexProcessExited as exc:
        yield _line(
            {
                "type": "transport_error",
                "error": (
                    "codex_process_exited"
                ),
                "message": str(exc),
            }
        )

    finally:
        for interaction_id in (
            pending_interactions
        ):
            pending = interactions.take(
                interaction_id
            )

            if pending is None:
                continue

            try:
                await codex.respond(
                    pending.request_id,
                    error={
                        "code": -32000,
                        "message": (
                            "query stream closed "
                            "before interaction "
                            "response"
                        ),
                    },
                )
            except Exception:
                # Cleanup must not mask the
                # original stream termination.
                pass

        codex.unsubscribe_thread(
            thread_id
        )


def create_query_router(
    codex,
    interaction_registry: (
        InteractionRegistry | None
    ) = None,
) -> APIRouter:
    router = APIRouter()

    interactions = (
        interaction_registry
        if interaction_registry is not None
        else InteractionRegistry()
    )

    @router.post(
        "/v1/interactions/"
        "{interaction_id}/respond"
    )
    async def respond_to_interaction(
        interaction_id: str,
        response: CodexInteractionResponse,
    ):
        if codex is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                },
            )

        pending = interactions.take(
            interaction_id
        )

        if pending is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": (
                        "interaction_not_found"
                    ),
                },
            )

        try:
            await codex.respond(
                pending.request_id,
                result=response.result,
            )

        except CodexProcessExited as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                    "message": str(exc),
                },
            ) from exc

        return {
            "responded": True,
            "interaction_id": interaction_id,
            "thread_id": (
                pending.thread_id
            ),
            "method": pending.method,
        }

    @router.post(
        "/v1/threads/{thread_id}/turns/{turn_id}/interrupt"
    )
    async def interrupt_turn(
        thread_id: str,
        turn_id: str,
    ):
        if codex is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                },
            )

        try:
            await codex.request(
                "turn/interrupt",
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                },
            )

        except CodexRequestError as exc:
            raise _request_error(
                exc
            ) from exc

        except CodexProcessExited as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "codex_unavailable",
                    "message": str(exc),
                },
            ) from exc

        return {
            "interrupted": True,
            "thread_id": thread_id,
            "turn_id": turn_id,
        }

    @router.post("/v1/query")
    async def query(
        req: CodexQueryRequest,
    ):
        if codex is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": (
                        "codex_unavailable"
                    ),
                },
            )

        if (
            req.thread_id is not None
            and req.tools is not None
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": (
                        "thread_tool_policy_is_fixed"
                    ),
                    "message": (
                        "tools policy is selected "
                        "when the Codex thread is "
                        "created; omit tools when "
                        "resuming an existing thread"
                    ),
                },
            )

        thread_id: str

        try:
            if req.thread_id is None:
                tools = (
                    req.tools
                    or ToolsPolicy.ENABLED
                )

                start_params = {
                    "cwd": req.cwd,
                    "ephemeral": (
                        req.ephemeral
                    ),
                    "sandbox": (
                        req.sandbox
                    ),
                    "approvalPolicy": (
                        "never"
                    ),
                    "model": req.model,
                }

                if (
                    tools
                    == ToolsPolicy.DISABLED
                ):
                    start_params[
                        "config"
                    ] = (
                        await _disabled_tools_config(
                            codex,
                            cwd=req.cwd,
                        )
                    )

                started = (
                    await codex.request(
                        "thread/start",
                        start_params,
                    )
                )

                thread = (
                    started.get("thread")
                    if isinstance(
                        started,
                        dict,
                    )
                    else None
                )

                if (
                    not isinstance(
                        thread,
                        dict,
                    )
                    or not isinstance(
                        thread.get("id"),
                        str,
                    )
                ):
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": (
                                "invalid_codex_response"
                            ),
                            "message": (
                                "thread/start "
                                "did not return "
                                "a thread id"
                            ),
                        },
                    )

                thread_id = thread["id"]

            else:
                thread_id = req.thread_id

                await codex.request(
                    "thread/resume",
                    {
                        "threadId": (
                            thread_id
                        ),
                        "excludeTurns": True,
                    },
                )

            try:
                codex.subscribe_thread(
                    thread_id
                )

            except RuntimeError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": (
                            "thread_busy"
                        ),
                        "thread_id": (
                            thread_id
                        ),
                        "message": str(exc),
                    },
                ) from exc

            try:
                started_turn = (
                    await codex.request(
                        "turn/start",
                        {
                            "threadId": (
                                thread_id
                            ),
                            "input": [
                                {
                                    "type": (
                                        "text"
                                    ),
                                    "text": (
                                        req.input
                                    ),
                                    "text_elements": [],
                                }
                            ],
                            "model": (
                                req.model
                            ),
                            "effort": (
                                req.reasoning_effort
                            ),
                        },
                    )
                )

            except BaseException:
                codex.unsubscribe_thread(
                    thread_id
                )
                raise

            turn = (
                started_turn.get("turn")
                if isinstance(
                    started_turn,
                    dict,
                )
                else None
            )

            if (
                not isinstance(turn, dict)
                or not isinstance(
                    turn.get("id"),
                    str,
                )
            ):
                codex.unsubscribe_thread(
                    thread_id
                )

                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": (
                            "invalid_codex_response"
                        ),
                        "message": (
                            "turn/start did not "
                            "return a turn id"
                        ),
                    },
                )

            turn_id = turn["id"]

        except CodexRequestError as exc:
            raise _request_error(
                exc
            ) from exc

        except CodexProcessExited as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": (
                        "codex_unavailable"
                    ),
                    "message": str(exc),
                },
            ) from exc

        return StreamingResponse(
            _stream_turn(
                codex,
                thread_id=thread_id,
                turn_id=turn_id,
                interactions=interactions,
            ),
            media_type=(
                "application/x-ndjson"
            ),
            headers={
                "X-Codex-Thread-Id": (
                    thread_id
                ),
                "X-Codex-Turn-Id": (
                    turn_id
                ),
            },
        )

    return router
