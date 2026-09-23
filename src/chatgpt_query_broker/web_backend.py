from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from .query_backend import (
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


class WebQueryBackend:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        request_timeout_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.request_timeout_seconds = (
            request_timeout_seconds
        )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": (
                    f"Bearer {self.api_key}"
                ),
                "Accept": "application/json",
            },
            timeout=self.request_timeout_seconds,
            transport=transport,
        )

    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities:
        # The provider session endpoint is currently
        # whole-response, not token streaming.
        return QueryBackendCapabilities(
            streaming=False,
            persistent_conversations=True,
            interruption=False,
            interactive_requests=False,
            tool_policy=False,
            sandbox=False,
        )

    async def start(
        self,
    ) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/health",
            authenticated=False,
        )

        payload = self._json_object(
            response,
            operation="provider health",
        )

        if payload.get("ok") is not True:
            raise QueryBackendUnavailable(
                "web provider health check "
                "did not report ok"
            )

        return payload

    async def aclose(
        self,
    ) -> None:
        await self._client.aclose()

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle:
        self._validate_request_policy(
            request
        )

        created = (
            request.conversation_id
            is None
        )

        if created:
            conversation_id = (
                "qb-"
                + uuid.uuid4().hex
            )

            await self._create_session(
                conversation_id=(
                    conversation_id
                ),
                request=request,
            )

        else:
            conversation_id = (
                request.conversation_id
            )

            assert conversation_id is not None

            await self._validate_session(
                conversation_id=(
                    conversation_id
                ),
                request=request,
            )

        execution_id = uuid.uuid4().hex

        return QueryHandle(
            conversation_id=conversation_id,
            execution_id=execution_id,
            events=self._completion_events(
                conversation_id=(
                    conversation_id
                ),
                execution_id=execution_id,
                request=request,
                delete_on_close=(
                    created
                    and request.ephemeral
                ),
            ),
        )

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None:
        raise QueryBackendPolicyError(
            error=(
                "backend_interruption_unsupported"
            ),
            message=(
                "web backend does not support "
                "interrupting an in-flight turn"
            ),
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict[str, Any],
    ) -> QueryInteractionReceipt:
        raise QueryInteractionNotFound(
            interaction_id
        )

    def _validate_request_policy(
        self,
        request: QueryRequest,
    ) -> None:
        if request.attachments:
            raise QueryBackendPolicyError(
                error=(
                    "backend_attachments_unsupported"
                ),
                message=(
                    "web backend does not support "
                    "attachments"
                ),
            )

        # ENABLED is compatible with the provider's
        # normal behavior. DISABLED cannot currently
        # be enforced through its provider-session API.
        if request.tools == ToolsPolicy.DISABLED:
            raise QueryBackendPolicyError(
                error=(
                    "backend_tool_policy_unsupported"
                ),
                message=(
                    "web backend cannot enforce "
                    "tools=disabled"
                ),
            )

        # q sends read-only by default. Treat that as
        # the compatibility no-op. Stronger sandbox
        # selections cannot be represented here.
        if request.sandbox != "read-only":
            raise QueryBackendPolicyError(
                error=(
                    "backend_sandbox_unsupported"
                ),
                message=(
                    "web backend does not support "
                    f"sandbox {request.sandbox!r}"
                ),
            )

    async def _create_session(
        self,
        *,
        conversation_id: str,
        request: QueryRequest,
    ) -> None:
        response = await self._request(
            "POST",
            "/v1/sessions",
            json={
                "session_id": conversation_id,
                "model": request.model,
                "reasoning_effort": (
                    request.reasoning_effort
                ),
                "conversation_policy": (
                    "regular"
                ),
            },
        )

        if response.status_code == 409:
            raise QueryBackendRequestError(
                code=409,
                message=(
                    "web provider session "
                    "identity collision"
                ),
                data=self._response_data(
                    response
                ),
            )

        self._raise_provider_error(
            response,
            operation="create session",
        )

        payload = self._json_object(
            response,
            operation="create session",
        )

        if (
            payload.get("session_id")
            != conversation_id
        ):
            raise QueryBackendProtocolError(
                "web provider returned an "
                "unexpected session id"
            )

    async def _validate_session(
        self,
        *,
        conversation_id: str,
        request: QueryRequest,
    ) -> None:
        response = await self._request(
            "GET",
            (
                "/v1/sessions/"
                + quote(
                    conversation_id,
                    safe="",
                )
            ),
        )

        if response.status_code == 404:
            raise QueryBackendPolicyError(
                error="conversation_not_found",
                message=(
                    "web provider session "
                    f"{conversation_id!r} "
                    "does not exist"
                ),
            )

        self._raise_provider_error(
            response,
            operation="get session",
        )

        payload = self._json_object(
            response,
            operation="get session",
        )

        if (
            payload.get("session_id")
            != conversation_id
        ):
            raise QueryBackendProtocolError(
                "web provider returned an "
                "unexpected session id"
            )

        model = payload.get("model")
        level = payload.get("level")

        if (
            model != request.model
            or level
            != request.reasoning_effort
        ):
            raise QueryBackendPolicyError(
                error=(
                    "conversation_configuration_mismatch"
                ),
                message=(
                    "web provider session is "
                    "pinned to a different model "
                    "or reasoning effort"
                ),
            )

    async def _completion_events(
        self,
        *,
        conversation_id: str,
        execution_id: str,
        request: QueryRequest,
        delete_on_close: bool,
    ) -> AsyncIterator[
        dict[str, Any]
    ]:
        try:
            response = await self._request(
                "POST",
                (
                    "/v1/sessions/"
                    + quote(
                        conversation_id,
                        safe="",
                    )
                    + "/completions"
                ),
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                request.input
                            ),
                        }
                    ]
                },
            )

            if response.status_code == 404:
                raise QueryBackendPolicyError(
                    error=(
                        "conversation_not_found"
                    ),
                    message=(
                        "web provider session "
                        f"{conversation_id!r} "
                        "does not exist"
                    ),
                )

            self._raise_provider_error(
                response,
                operation=(
                    "session completion"
                ),
            )

            payload = self._json_object(
                response,
                operation=(
                    "session completion"
                ),
            )

            text = self._completion_text(
                payload
            )

            if text:
                yield {
                    "type": "delta",
                    "conversation_id": (
                        conversation_id
                    ),
                    "execution_id": (
                        execution_id
                    ),
                    "delta": text,
                }

            yield {
                "type": "completed",
                "conversation_id": (
                    conversation_id
                ),
                "execution_id": (
                    execution_id
                ),
                "status": "completed",
                "error": None,
            }

        finally:
            if delete_on_close:
                try:
                    await self._request(
                        "DELETE",
                        (
                            "/v1/sessions/"
                            + quote(
                                conversation_id,
                                safe="",
                            )
                        ),
                    )
                except Exception:
                    # Ephemeral cleanup is best effort;
                    # it must not replace the query result.
                    pass

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        json: dict[str, Any]
        | None = None,
    ) -> httpx.Response:
        headers = None

        if not authenticated:
            headers = {
                "Authorization": "",
            }

        try:
            return await self._client.request(
                method,
                path,
                json=json,
                headers=headers,
            )

        except httpx.RequestError as exc:
            raise QueryBackendUnavailable(
                "web provider request failed: "
                f"{exc}"
            ) from exc

    def _raise_provider_error(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> None:
        if response.is_success:
            return

        data = self._response_data(
            response
        )

        if response.status_code in {
            400,
            422,
        }:
            detail = (
                data.get("detail")
                if isinstance(data, dict)
                else None
            )

            error = (
                detail.get("error")
                if isinstance(detail, dict)
                else None
            )

            message = (
                detail.get("message")
                if isinstance(detail, dict)
                else None
            )

            raise QueryBackendPolicyError(
                error=(
                    str(error)
                    if error
                    else (
                        "web_provider_rejected_request"
                    )
                ),
                message=(
                    str(message)
                    if message
                    else (
                        f"web provider rejected "
                        f"{operation}"
                    )
                ),
            )

        raise QueryBackendRequestError(
            code=response.status_code,
            message=(
                f"web provider {operation} "
                f"failed with HTTP "
                f"{response.status_code}"
            ),
            data=data,
        )

    @staticmethod
    def _response_data(
        response: httpx.Response,
    ) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _json_object(
        response: httpx.Response,
        *,
        operation: str,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise QueryBackendProtocolError(
                f"web provider {operation} "
                "returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise QueryBackendProtocolError(
                f"web provider {operation} "
                "did not return an object"
            )

        return payload

    @staticmethod
    def _completion_text(
        payload: dict[str, Any],
    ) -> str:
        choices = payload.get(
            "choices"
        )

        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(
                choices[0],
                dict,
            )
        ):
            raise QueryBackendProtocolError(
                "web provider completion "
                "did not contain choices"
            )

        message = choices[0].get(
            "message"
        )

        if not isinstance(message, dict):
            raise QueryBackendProtocolError(
                "web provider completion "
                "did not contain a message"
            )

        content = message.get(
            "content"
        )

        if not isinstance(content, str):
            raise QueryBackendProtocolError(
                "web provider completion "
                "content is not text"
            )

        return content
