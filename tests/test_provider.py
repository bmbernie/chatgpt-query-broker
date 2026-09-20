import asyncio
import json

import httpx
import pytest

from chatgpt_worker_broker.provider import (
    ProviderClient,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)


def test_create_session_contract_and_auth():
    async def run():
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["method"] = request.method
            observed["path"] = request.url.path
            observed["authorization"] = request.headers.get(
                "authorization"
            )
            observed["json"] = json.loads(request.content)

            return httpx.Response(
                201,
                json={
                    "session_id": "re-high",
                    "model": "chatgpt-5.6-sol-web",
                    "level": "high",
                    "conversation_policy": "regular",
                    "state": "ready",
                },
            )

        client = ProviderClient(
            "http://provider.test",
            "secret-token",
            transport=httpx.MockTransport(handler),
        )

        try:
            session = await client.create_session(
                "re-high",
                "chatgpt-5.6-sol-web",
                "high",
            )
        finally:
            await client.aclose()

        assert observed == {
            "method": "POST",
            "path": "/v1/sessions",
            "authorization": "Bearer secret-token",
            "json": {
                "session_id": "re-high",
                "model": "chatgpt-5.6-sol-web",
                "reasoning_effort": "high",
                "conversation_policy": "regular",
            },
        }

        assert session.session_id == "re-high"
        assert session.model == "chatgpt-5.6-sol-web"
        assert session.level == "high"
        assert session.conversation_policy == "regular"
        assert session.state == "ready"

    asyncio.run(run())


def test_create_session_conflict_is_typed():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                json={"detail": "session already exists"},
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            with pytest.raises(ProviderSessionConflict):
                await client.create_session(
                    "re-high",
                    "chatgpt-5.6-sol-web",
                    "high",
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_get_session_parses_provider_response():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/v1/sessions/review-xhigh"

            return httpx.Response(
                200,
                json={
                    "session_id": "review-xhigh",
                    "model": "chatgpt-5.6-sol-web",
                    "level": "xhigh",
                    "state": "ready",
                },
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            session = await client.get_session(
                "review-xhigh"
            )
        finally:
            await client.aclose()

        assert session.session_id == "review-xhigh"
        assert session.level == "xhigh"
        assert session.state == "ready"

    asyncio.run(run())


def test_get_missing_session_is_typed():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"detail": "session not found"},
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            with pytest.raises(ProviderSessionNotFound):
                await client.get_session("missing")
        finally:
            await client.aclose()

    asyncio.run(run())


def test_list_sessions_contract():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/v1/sessions"

            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "session_id": "re-high",
                            "model": "chatgpt-5.6-sol-web",
                            "level": "high",
                            "state": "ready",
                        },
                        {
                            "session_id": "review-xhigh",
                            "model": "chatgpt-5.6-sol-web",
                            "level": "xhigh",
                            "state": "ready",
                        },
                    ],
                },
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            sessions = await client.list_sessions()
        finally:
            await client.aclose()

        assert [
            (session.session_id, session.level)
            for session in sessions
        ] == [
            ("re-high", "high"),
            ("review-xhigh", "xhigh"),
        ]

    asyncio.run(run())


def test_delete_session_success_and_not_found():
    async def run():
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1

            assert request.method == "DELETE"

            if request.url.path == "/v1/sessions/re-high":
                return httpx.Response(204)

            return httpx.Response(
                404,
                json={"detail": "session not found"},
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            await client.delete_session("re-high")

            with pytest.raises(ProviderSessionNotFound):
                await client.delete_session("missing")
        finally:
            await client.aclose()

        assert calls == 2

    asyncio.run(run())


def test_complete_session_contract_and_parsing():
    async def run():
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["method"] = request.method
            observed["path"] = request.url.path
            observed["json"] = json.loads(request.content)

            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "session_id": "re-high",
                    "model": "chatgpt-5.6-sol-web",
                    "level": "high",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "analysis complete",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
            )

        client = ProviderClient(
            "http://provider.test",
            "token",
            transport=httpx.MockTransport(handler),
        )

        try:
            result = await client.complete_session(
                "re-high",
                [
                    {
                        "role": "user",
                        "content": "analyze this",
                    }
                ],
            )
        finally:
            await client.aclose()

        assert observed == {
            "method": "POST",
            "path": "/v1/sessions/re-high/completions",
            "json": {
                "messages": [
                    {
                        "role": "user",
                        "content": "analyze this",
                    }
                ]
            },
        }

        assert result.response_id == "chatcmpl-test"
        assert result.session_id == "re-high"
        assert result.model == "chatgpt-5.6-sol-web"
        assert result.level == "high"
        assert result.content == "analysis complete"

    asyncio.run(run())
