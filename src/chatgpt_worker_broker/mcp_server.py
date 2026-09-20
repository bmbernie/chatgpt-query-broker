from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


DEFAULT_BROKER_URL = "http://127.0.0.1:8792"
DEFAULT_MODEL = "chatgpt-5.6-sol-web"


class BrokerAPI:
    def __init__(
        self,
        base_url: str = DEFAULT_BROKER_URL,
        *,
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    path,
                    json=json,
                )
            except httpx.HTTPError as exc:
                raise ToolError(
                    f"worker broker transport error: {exc}"
                ) from exc

        if response.is_error:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise ToolError(
                f"worker broker HTTP {response.status_code}: "
                f"{detail}"
            )

        if response.status_code == 204:
            return {"ok": True}

        data = response.json()

        if not isinstance(data, dict):
            raise ToolError(
                "worker broker returned a non-object response"
            )

        return data


def build_mcp_server(
    broker: BrokerAPI | None = None,
) -> MCPServer:
    api = broker or BrokerAPI(
        base_url=os.getenv(
            "CHATGPT_WORKER_BROKER_URL",
            DEFAULT_BROKER_URL,
        ),
        timeout=float(
            os.getenv(
                "CHATGPT_WORKER_BROKER_MCP_TIMEOUT_SECONDS",
                "600",
            )
        ),
    )

    mcp = MCPServer("ChatGPT Worker Broker")

    read_only = ToolAnnotations(
        read_only_hint=True,
        idempotent_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    )

    idempotent_write = ToolAnnotations(
        read_only_hint=False,
        idempotent_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    )

    write = ToolAnnotations(
        read_only_hint=False,
        idempotent_hint=False,
        destructive_hint=False,
        open_world_hint=False,
    )

    @mcp.tool(
        description=(
            "List all persistent ChatGPT workers, including role, "
            "reasoning tier, lifecycle state, and provider-session binding."
        ),
        annotations=read_only,
    )
    async def worker_list() -> dict[str, Any]:
        return await api.request(
            "GET",
            "/v1/workers",
        )

    @mcp.tool(
        description=(
            "Get the current state and configuration of one worker."
        ),
        annotations=read_only,
    )
    async def worker_status(
        worker_id: str,
    ) -> dict[str, Any]:
        return await api.request(
            "GET",
            f"/v1/workers/{worker_id}",
        )

    @mcp.tool(
        description=(
            "Get a durable broker operation and its result, error, "
            "or current execution state."
        ),
        annotations=read_only,
    )
    async def worker_result(
        operation_id: str,
    ) -> dict[str, Any]:
        return await api.request(
            "GET",
            f"/v1/operations/{operation_id}",
        )

    @mcp.tool(
        description=(
            "Create a persistent browser-worker definition. "
            "Workers are initially sleeping and lazily acquire a "
            "ChatGPT browser session when awakened or sent work."
        ),
        annotations=write,
    )
    async def worker_create(
        worker_id: str,
        role: Literal[
            "planner",
            "specialist",
            "reviewer",
            "adjudicator",
        ],
        reasoning_level: Literal["high", "xhigh"],
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        return await api.request(
            "POST",
            "/v1/workers",
            json={
                "worker_id": worker_id,
                "role": role,
                "model": model,
                "reasoning_level": reasoning_level,
            },
        )

    @mcp.tool(
        description=(
            "Wake a worker. This creates or reconciles its pinned "
            "browser session and leaves it ready for work."
        ),
        annotations=idempotent_write,
    )
    async def worker_wake(
        worker_id: str,
    ) -> dict[str, Any]:
        return await api.request(
            "POST",
            f"/v1/workers/{worker_id}/wake",
        )

    @mcp.tool(
        description=(
            "Put a worker to sleep and close its provider browser "
            "session. The durable worker definition remains."
        ),
        annotations=idempotent_write,
    )
    async def worker_sleep(
        worker_id: str,
    ) -> dict[str, Any]:
        return await api.request(
            "POST",
            f"/v1/workers/{worker_id}/sleep",
        )

    @mcp.tool(
        description=(
            "Send one task to a persistent worker. operation_id is "
            "the durable idempotency key: retries MUST reuse the same "
            "operation_id so the prompt is never submitted twice."
        ),
        annotations=idempotent_write,
    )
    async def worker_send(
        worker_id: str,
        operation_id: str,
        message: str,
    ) -> dict[str, Any]:
        return await api.request(
            "POST",
            f"/v1/workers/{worker_id}/operations",
            json={
                "operation_id": operation_id,
                "messages": [
                    {
                        "role": "user",
                        "content": message,
                    }
                ],
            },
        )

    return mcp


def main() -> None:
    build_mcp_server().run()


if __name__ == "__main__":
    main()
