import json

import httpx
import pytest

from chatgpt_query_broker.query_backend import (
    QueryAttachment,
    QueryBackendPolicyError,
    QueryRequest,
    ToolsPolicy,
)
from chatgpt_query_broker.web_backend import (
    WebQueryBackend,
)


def request(
    **overrides,
):
    values = {
        "input": "hello",
        "cwd": "/tmp",
        "model": "gpt-a",
        "reasoning_effort": "xhigh",
        "ephemeral": False,
    }
    values.update(overrides)

    return QueryRequest(**values)


@pytest.mark.asyncio
async def test_web_backend_health():
    async def handler(req):
        assert req.url.path == "/health"

        return httpx.Response(
            200,
            json={
                "ok": True,
                "backend": "browser",
            },
        )

    backend = WebQueryBackend(
        base_url="http://provider.test",
        api_key="secret",
        transport=httpx.MockTransport(
            handler
        ),
    )

    try:
        result = await backend.start()

        assert result == {
            "ok": True,
            "backend": "browser",
        }

    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_new_web_query_creates_session_and_completes():
    calls = []

    async def handler(req):
        body = (
            json.loads(req.content)
            if req.content
            else None
        )

        calls.append(
            (
                req.method,
                req.url.path,
                body,
            )
        )

        if req.url.path == "/v1/sessions":
            assert (
                req.headers["authorization"]
                == "Bearer secret"
            )

            return httpx.Response(
                201,
                json={
                    "session_id": body[
                        "session_id"
                    ],
                    "model": "gpt-a",
                    "level": "xhigh",
                    "conversation_policy": (
                        "regular"
                    ),
                    "state": "ready",
                },
            )

        if req.url.path.endswith(
            "/completions"
        ):
            return httpx.Response(
                200,
                json={
                    "session_id": (
                        req.url.path.split(
                            "/"
                        )[3]
                    ),
                    "model": "gpt-a",
                    "level": "xhigh",
                    "choices": [
                        {
                            "message": {
                                "role": (
                                    "assistant"
                                ),
                                "content": (
                                    "web answer"
                                ),
                            }
                        }
                    ],
                },
            )

        raise AssertionError(
            req.url.path
        )

    backend = WebQueryBackend(
        base_url="http://provider.test",
        api_key="secret",
        transport=httpx.MockTransport(
            handler
        ),
    )

    try:
        handle = await backend.start_query(
            request()
        )

        assert handle.conversation_id.startswith(
            "qb-"
        )

        events = [
            event
            async for event
            in handle.events
        ]

        assert events[0]["type"] == "delta"
        assert (
            events[0]["delta"]
            == "web answer"
        )

        assert events[1] == {
            "type": "completed",
            "conversation_id": (
                handle.conversation_id
            ),
            "execution_id": (
                handle.execution_id
            ),
            "status": "completed",
            "error": None,
        }

        assert calls[0][0:2] == (
            "POST",
            "/v1/sessions",
        )

        assert calls[0][2][
            "reasoning_effort"
        ] == "xhigh"

        assert calls[1][2] == {
            "messages": [
                {
                    "role": "user",
                    "content": "hello",
                }
            ]
        }

    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_web_query_resumes_existing_session():
    async def handler(req):
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "session_id": (
                        "existing"
                    ),
                    "model": "gpt-a",
                    "level": "xhigh",
                    "conversation_policy": (
                        "regular"
                    ),
                    "state": "ready",
                },
            )

        if req.method == "POST":
            return httpx.Response(
                200,
                json={
                    "session_id": (
                        "existing"
                    ),
                    "model": "gpt-a",
                    "level": "xhigh",
                    "choices": [
                        {
                            "message": {
                                "role": (
                                    "assistant"
                                ),
                                "content": (
                                    "continued"
                                ),
                            }
                        }
                    ],
                },
            )

        raise AssertionError(
            req.method
        )

    backend = WebQueryBackend(
        base_url="http://provider.test",
        api_key="secret",
        transport=httpx.MockTransport(
            handler
        ),
    )

    try:
        handle = await backend.start_query(
            request(
                conversation_id="existing"
            )
        )

        assert (
            handle.conversation_id
            == "existing"
        )

        events = [
            event
            async for event
            in handle.events
        ]

        assert (
            events[0]["delta"]
            == "continued"
        )

    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_web_backend_rejects_unenforceable_policy():
    backend = WebQueryBackend(
        base_url="http://provider.test",
        api_key="secret",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                500
            )
        ),
    )

    try:
        with pytest.raises(
            QueryBackendPolicyError,
        ) as exc_info:
            await backend.start_query(
                request(
                    tools=(
                        ToolsPolicy.DISABLED
                    )
                )
            )

        assert (
            exc_info.value.error
            == "backend_tool_policy_unsupported"
        )

        with pytest.raises(
            QueryBackendPolicyError,
        ):
            await backend.start_query(
                request(
                    sandbox=(
                        "workspace-write"
                    )
                )
            )

    finally:
        await backend.aclose()



@pytest.mark.asyncio
async def test_web_backend_rejects_attachments():
    backend = WebQueryBackend(
        base_url="http://provider.test",
        api_key="secret",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                500
            )
        ),
    )

    try:
        with pytest.raises(
            QueryBackendPolicyError,
        ) as exc_info:
            await backend.start_query(
                request(
                    attachments=(
                        QueryAttachment(
                            kind="image",
                            path="/tmp/example.png",
                        ),
                    ),
                )
            )

        assert (
            exc_info.value.error
            == "backend_attachments_unsupported"
        )

    finally:
        await backend.aclose()
