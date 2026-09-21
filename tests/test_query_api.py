import asyncio
import json

from fastapi.testclient import (
    TestClient,
)

from chatgpt_worker_broker.app import (
    create_app,
)
from chatgpt_worker_broker.codex_models import (
    CodexNotification,
    CodexServerRequest,
)
from chatgpt_worker_broker.query_api import (
    NO_TOOLS_CONFIG,
)
from chatgpt_worker_broker.service import (
    BrokerService,
)
from chatgpt_worker_broker.store import (
    BrokerStore,
)


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.subscribed = set()
        self.queues = {}
        self.responses = []

    async def request(
        self,
        method,
        params=None,
        **kwargs,
    ):
        params = params or {}

        self.calls.append(
            (method, params)
        )

        if method == "config/read":
            return {
                "config": {
                    "mcp_servers": {
                        "example": {
                            "enabled": True,
                        },
                    },
                },
            }

        if method == "thread/start":
            return {
                "thread": {
                    "id": "thread-new",
                },
                "model": params["model"],
                "modelProvider": "openai",
            }

        if method == "thread/resume":
            return {
                "thread": {
                    "id": (
                        params["threadId"]
                    ),
                },
            }

        if method == "turn/start":
            thread_id = (
                params["threadId"]
            )
            turn_id = (
                f"turn-{thread_id}"
            )

            queue = self.queues[
                thread_id
            ]

            queue.put_nowait(
                CodexNotification(
                    method=(
                        "item/agentMessage/delta"
                    ),
                    params={
                        "threadId": (
                            thread_id
                        ),
                        "turnId": turn_id,
                        "itemId": "item-1",
                        "delta": "hello",
                    },
                )
            )

            queue.put_nowait(
                CodexNotification(
                    method="turn/completed",
                    params={
                        "threadId": (
                            thread_id
                        ),
                        "turn": {
                            "id": turn_id,
                            "status": (
                                "completed"
                            ),
                            "items": [],
                            "error": None,
                        },
                    },
                )
            )

            return {
                "turn": {
                    "id": turn_id,
                    "status": (
                        "inProgress"
                    ),
                    "items": [],
                },
            }

        raise AssertionError(
            f"unexpected method {method}"
        )

    def subscribe_thread(
        self,
        thread_id,
    ):
        if thread_id in self.subscribed:
            raise RuntimeError(
                "already subscribed"
            )

        self.subscribed.add(
            thread_id
        )
        self.queues[
            thread_id
        ] = asyncio.Queue()

    def unsubscribe_thread(
        self,
        thread_id,
    ):
        self.subscribed.discard(
            thread_id
        )
        self.queues.pop(
            thread_id,
            None,
        )

    async def next_thread_message(
        self,
        thread_id,
        **kwargs,
    ):
        return await self.queues[
            thread_id
        ].get()

    async def respond(
        self,
        request_id,
        *,
        result=None,
        error=None,
    ):
        self.responses.append(
            {
                "request_id": (
                    request_id
                ),
                "result": result,
                "error": error,
            }
        )


def make_client(
    tmp_path,
    codex,
):
    store = BrokerStore(
        tmp_path / "broker.sqlite3"
    )

    service = BrokerService(
        store,
        object(),
    )

    app = create_app(
        store=store,
        service=service,
        codex=codex,
    )

    return TestClient(app)


def events(response):
    return [
        json.loads(line)
        for line in (
            response.text.splitlines()
        )
        if line.strip()
    ]


def base_request():
    return {
        "input": "hello",
        "cwd": "/tmp",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "xhigh",
    }


def test_new_query_streams_codex_turn(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    response = client.post(
        "/v1/query",
        json=base_request(),
    )

    assert response.status_code == 200

    assert (
        response.headers[
            "x-codex-thread-id"
        ]
        == "thread-new"
    )

    data = events(response)

    assert [
        event["type"]
        for event in data
    ] == [
        "thread",
        "turn",
        "delta",
        "completed",
    ]

    assert data[2]["delta"] == "hello"
    assert (
        data[3]["status"]
        == "completed"
    )

    thread_start = next(
        params
        for method, params in codex.calls
        if method == "thread/start"
    )

    assert "config" not in thread_start
    assert (
        thread_start["ephemeral"]
        is True
    )
    assert (
        thread_start["sandbox"]
        == "read-only"
    )


def test_new_query_can_disable_tools(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["tools"] = "disabled"

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 200

    thread_start = next(
        params
        for method, params in codex.calls
        if method == "thread/start"
    )

    config = thread_start["config"]

    for key, value in (
        NO_TOOLS_CONFIG.items()
    ):
        assert config[key] == value

    assert config["mcp_servers"] == {
        "example": {
            "enabled": False,
        },
    }


def test_existing_thread_is_resumed(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["thread_id"] = (
        "thread-existing"
    )

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 200

    assert (
        response.headers[
            "x-codex-thread-id"
        ]
        == "thread-existing"
    )

    resume = next(
        params
        for method, params in codex.calls
        if method == "thread/resume"
    )

    assert resume == {
        "threadId": (
            "thread-existing"
        ),
        "excludeTurns": True,
    }


def test_existing_thread_rejects_tool_policy_change(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["thread_id"] = (
        "thread-existing"
    )
    payload["tools"] = "disabled"

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"][
            "error"
        ]
        == "thread_tool_policy_is_fixed"
    )

    assert codex.calls == []


def test_query_returns_503_without_codex(
    tmp_path,
):
    client = make_client(
        tmp_path,
        None,
    )

    response = client.post(
        "/v1/query",
        json=base_request(),
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"][
            "error"
        ]
        == "codex_unavailable"
    )
