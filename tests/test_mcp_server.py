import asyncio
import json

import httpx
from mcp import Client

from chatgpt_worker_broker.mcp_server import (
    BrokerAPI,
    build_mcp_server,
)


def test_mcp_tool_catalog_and_translation():
    async def run():
        observed = []

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            body = (
                json.loads(request.content)
                if request.content
                else None
            )

            observed.append(
                (
                    request.method,
                    request.url.path,
                    body,
                )
            )

            if (
                request.method == "GET"
                and request.url.path == "/v1/workers"
            ):
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [],
                    },
                )

            if (
                request.method == "POST"
                and request.url.path
                == "/v1/workers/re-high/operations"
            ):
                return httpx.Response(
                    200,
                    json={
                        "operation_id": "mcp-op-001",
                        "worker_id": "re-high",
                        "state": "completed",
                        "request": body,
                        "result": {
                            "content": "mcp-pass",
                        },
                        "error": None,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "completed_at": "2026-01-01T00:00:01+00:00",
                    },
                )

            return httpx.Response(
                404,
                json={"detail": "not found"},
            )

        api = BrokerAPI(
            "http://broker.test",
            transport=httpx.MockTransport(handler),
        )

        mcp = build_mcp_server(api)

        async with Client(mcp) as client:
            tools = await client.list_tools()

            names = sorted(
                tool.name
                for tool in tools.tools
            )

            assert names == [
                "worker_create",
                "worker_list",
                "worker_result",
                "worker_send",
                "worker_sleep",
                "worker_status",
                "worker_wake",
            ]

            listed = await client.call_tool(
                "worker_list",
                {},
            )
            assert listed.is_error is False

            sent = await client.call_tool(
                "worker_send",
                {
                    "worker_id": "re-high",
                    "operation_id": "mcp-op-001",
                    "message": "Reply with mcp-pass",
                },
            )
            assert sent.is_error is False

        assert observed == [
            (
                "GET",
                "/v1/workers",
                None,
            ),
            (
                "POST",
                "/v1/workers/re-high/operations",
                {
                    "operation_id": "mcp-op-001",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply with mcp-pass",
                        }
                    ],
                },
            ),
        ]

    asyncio.run(run())


def test_mcp_surfaces_broker_error():
    async def run():
        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            return httpx.Response(
                409,
                json={
                    "detail": {
                        "error": "worker_exists",
                    }
                },
            )

        api = BrokerAPI(
            "http://broker.test",
            transport=httpx.MockTransport(handler),
        )

        mcp = build_mcp_server(api)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "worker_create",
                {
                    "worker_id": "re-high",
                    "role": "specialist",
                    "reasoning_level": "high",
                },
            )

            assert result.is_error is True

    asyncio.run(run())
