from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from .codex_models import (
    CodexNotification,
    CodexProcessExited,
    CodexRequestError,
    CodexServerRequest,
)
from .query_backend import (
    QueryBackendBusy,
    QueryBackendCapabilities,
    QueryBackendPolicyError,
    QueryBackendProtocolError,
    QueryBackendRequestError,
    QueryBackendUnavailable,
    QueryHandle,
    QueryInteractionNotFound,
    QueryInteractionReceipt,
    QueryRequest,
    ToolsPolicy,
)


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


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    request_id: int | str
    conversation_id: str
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
        conversation_id: str,
        method: str,
    ) -> str:
        interaction_id = uuid.uuid4().hex

        pending = PendingInteraction(
            request_id=request_id,
            conversation_id=conversation_id,
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


class CodexQueryBackend:
    def __init__(
        self,
        client,
        *,
        interaction_registry: (
            InteractionRegistry | None
        ) = None,
    ):
        self.client = client
        self.interactions = (
            interaction_registry
            if interaction_registry is not None
            else InteractionRegistry()
        )

    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities:
        return QueryBackendCapabilities(
            streaming=True,
            persistent_conversations=True,
            interruption=True,
            interactive_requests=True,
            tool_policy=True,
            sandbox=True,
        )

    async def start(
        self,
    ) -> dict[str, Any] | None:
        try:
            result = await self.client.start()
        except CodexProcessExited as exc:
            raise QueryBackendUnavailable(
                str(exc)
            ) from exc

        return (
            result
            if isinstance(result, dict)
            else None
        )

    async def aclose(
        self,
    ) -> None:
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        params: dict | None = None,
    ):
        try:
            return await self.client.request(
                method,
                params,
            )
        except CodexRequestError as exc:
            raise QueryBackendRequestError(
                code=exc.code,
                message=exc.message,
                data=exc.data,
            ) from exc
        except CodexProcessExited as exc:
            raise QueryBackendUnavailable(
                str(exc)
            ) from exc

    async def _disabled_tools_config(
        self,
        *,
        cwd: str,
    ) -> dict:
        result = await self._request(
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

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle:
        conversation_id: str

        if request.conversation_id is None:
            tools = (
                request.tools
                or ToolsPolicy.ENABLED
            )

            start_params = {
                "cwd": request.cwd,
                "ephemeral": request.ephemeral,
                "sandbox": request.sandbox,
                "approvalPolicy": (
                    "never"
                    if tools == ToolsPolicy.DISABLED
                    else "on-request"
                ),
                "approvalsReviewer": "user",
                "model": request.model,
            }

            if tools == ToolsPolicy.DISABLED:
                start_params["config"] = (
                    await self._disabled_tools_config(
                        cwd=request.cwd,
                    )
                )

            started = await self._request(
                "thread/start",
                start_params,
            )

            thread = (
                started.get("thread")
                if isinstance(started, dict)
                else None
            )

            if (
                not isinstance(thread, dict)
                or not isinstance(
                    thread.get("id"),
                    str,
                )
            ):
                raise QueryBackendProtocolError(
                    "thread/start did not return "
                    "a thread id"
                )

            conversation_id = thread["id"]

        else:
            if request.tools is not None:
                raise QueryBackendPolicyError(
                    error=(
                        "thread_tool_policy_is_fixed"
                    ),
                    message=(
                        "tools policy is selected "
                        "when the Codex thread is "
                        "created; omit tools when "
                        "resuming an existing thread"
                    ),
                )

            conversation_id = (
                request.conversation_id
            )

            await self._request(
                "thread/resume",
                {
                    "threadId": conversation_id,
                    "excludeTurns": True,
                },
            )

        try:
            self.client.subscribe_thread(
                conversation_id
            )
        except RuntimeError as exc:
            raise QueryBackendBusy(
                str(exc),
                conversation_id=conversation_id,
            ) from exc

        turn_input = [
            {
                "type": "text",
                "text": request.input,
                "text_elements": [],
            }
        ]

        for attachment in request.attachments:
            if attachment.kind == "image":
                input_type = "localImage"
            elif attachment.kind == "audio":
                input_type = "localAudio"
            else:
                raise QueryBackendPolicyError(
                    error="unsupported_attachment_type",
                    message=(
                        "unsupported attachment type: "
                        f"{attachment.kind}"
                    ),
                )

            turn_input.append(
                {
                    "type": input_type,
                    "path": attachment.path,
                }
            )

        try:
            started_turn = await self._request(
                "turn/start",
                {
                    "threadId": conversation_id,
                    "input": turn_input,
                    "model": request.model,
                    "effort": (
                        request.reasoning_effort
                    ),
                },
            )
        except BaseException:
            self.client.unsubscribe_thread(
                conversation_id
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
            self.client.unsubscribe_thread(
                conversation_id
            )

            raise QueryBackendProtocolError(
                "turn/start did not return "
                "a turn id"
            )

        execution_id = turn["id"]

        return QueryHandle(
            conversation_id=conversation_id,
            execution_id=execution_id,
            events=self._stream_events(
                conversation_id=conversation_id,
                execution_id=execution_id,
            ),
        )

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None:
        await self._request(
            "turn/interrupt",
            {
                "threadId": conversation_id,
                "turnId": execution_id,
            },
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict[str, Any],
    ) -> QueryInteractionReceipt:
        pending = self.interactions.take(
            interaction_id
        )

        if pending is None:
            raise QueryInteractionNotFound(
                interaction_id
            )

        try:
            await self.client.respond(
                pending.request_id,
                result=result,
            )
        except CodexProcessExited as exc:
            raise QueryBackendUnavailable(
                str(exc)
            ) from exc

        return QueryInteractionReceipt(
            interaction_id=interaction_id,
            conversation_id=(
                pending.conversation_id
            ),
            method=pending.method,
        )

    async def _stream_events(
        self,
        *,
        conversation_id: str,
        execution_id: str,
    ):
        pending_interactions: set[str] = set()

        try:
            while True:
                incoming = (
                    await self.client.next_thread_message(
                        conversation_id
                    )
                )

                if isinstance(
                    incoming,
                    CodexServerRequest,
                ):
                    interaction_id = (
                        self.interactions.register(
                            request_id=(
                                incoming.request_id
                            ),
                            conversation_id=(
                                conversation_id
                            ),
                            method=incoming.method,
                        )
                    )

                    pending_interactions.add(
                        interaction_id
                    )

                    yield {
                        "type": "server_request",
                        "interaction_id": (
                            interaction_id
                        ),
                        "conversation_id": (
                            conversation_id
                        ),
                        "execution_id": (
                            execution_id
                        ),
                        "method": incoming.method,
                        "params": incoming.params,
                    }

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
                    == execution_id
                ):
                    yield {
                        "type": "delta",
                        "conversation_id": (
                            conversation_id
                        ),
                        "execution_id": (
                            execution_id
                        ),
                        "item_id": params.get(
                            "itemId"
                        ),
                        "delta": params.get(
                            "delta",
                            "",
                        ),
                    }

                    continue

                if (
                    method == "error"
                    and params.get("turnId")
                    == execution_id
                ):
                    yield {
                        "type": "error",
                        "conversation_id": (
                            conversation_id
                        ),
                        "execution_id": (
                            execution_id
                        ),
                        "error": params.get(
                            "error"
                        ),
                        "will_retry": params.get(
                            "willRetry"
                        ),
                    }

                    continue

                if (
                    method == "turn/completed"
                    and (
                        params.get("turn")
                        or {}
                    ).get("id")
                    == execution_id
                ):
                    turn = (
                        params.get("turn")
                        or {}
                    )

                    yield {
                        "type": "completed",
                        "conversation_id": (
                            conversation_id
                        ),
                        "execution_id": (
                            execution_id
                        ),
                        "status": turn.get(
                            "status"
                        ),
                        "error": turn.get(
                            "error"
                        ),
                    }

                    return

                if (
                    method == "item/completed"
                    and params.get("turnId")
                    == execution_id
                ):
                    item = params.get("item") or {}

                    if (
                        item.get("type")
                        == "imageGeneration"
                    ):
                        saved_path = item.get(
                            "savedPath"
                        )

                        if (
                            isinstance(
                                saved_path,
                                str,
                            )
                            and saved_path
                        ):
                            lower_path = (
                                saved_path.lower()
                            )

                            if lower_path.endswith(
                                ".png"
                            ):
                                mime_type = (
                                    "image/png"
                                )
                            elif lower_path.endswith(
                                (".jpg", ".jpeg")
                            ):
                                mime_type = (
                                    "image/jpeg"
                                )
                            elif lower_path.endswith(
                                ".webp"
                            ):
                                mime_type = (
                                    "image/webp"
                                )
                            elif lower_path.endswith(
                                ".gif"
                            ):
                                mime_type = (
                                    "image/gif"
                                )
                            else:
                                mime_type = (
                                    "application/"
                                    "octet-stream"
                                )

                            yield {
                                "type": "artifact",
                                "conversation_id": (
                                    conversation_id
                                ),
                                "execution_id": (
                                    execution_id
                                ),
                                "artifact_id": (
                                    item.get("id")
                                ),
                                "artifact_type": (
                                    "image"
                                ),
                                "mime_type": (
                                    mime_type
                                ),
                                "path": saved_path,
                            }

                            continue

                yield {
                    "type": "event",
                    "method": method,
                    "params": params,
                }

        except CodexProcessExited as exc:
            yield {
                "type": "transport_error",
                "error": (
                    "backend_unavailable"
                ),
                "backend": "codex",
                "backend_error": (
                    "codex_process_exited"
                ),
                "message": str(exc),
            }

        finally:
            for interaction_id in (
                pending_interactions
            ):
                pending = (
                    self.interactions.take(
                        interaction_id
                    )
                )

                if pending is None:
                    continue

                try:
                    await self.client.respond(
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
                    pass

            self.client.unsubscribe_thread(
                conversation_id
            )
