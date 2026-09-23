import asyncio
import json

from fastapi.testclient import (
    TestClient,
)

from chatgpt_query_broker.app import (
    create_app,
)
from chatgpt_query_broker.codex_models import (
    CodexNotification,
    CodexServerRequest,
)
from chatgpt_query_broker.codex_backend import (
    CodexQueryBackend,
    InteractionRegistry,
    NO_TOOLS_CONFIG,
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
    _ = tmp_path

    backend = (
        CodexQueryBackend(codex)
        if codex is not None
        else None
    )

    app = create_app(
        query_backend=backend,
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


def test_query_api_forwards_local_image_attachment(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["input"] = "describe this"
    payload["attachments"] = [
        {
            "kind": "image",
            "path": "/tmp/example.png",
        },
    ]

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 200

    turn_start = next(
        params
        for method, params in codex.calls
        if method == "turn/start"
    )

    assert turn_start["input"] == [
        {
            "type": "text",
            "text": "describe this",
            "text_elements": [],
        },
        {
            "type": "localImage",
            "path": "/tmp/example.png",
        },
    ]




def test_query_api_forwards_generic_file_attachment(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["input"] = "inspect this file"
    payload["attachments"] = [
        {
            "kind": "file",
            "path": "/tmp/recording.wav",
        },
    ]

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 200

    turn_start = next(
        params
        for method, params in codex.calls
        if method == "turn/start"
    )

    assert turn_start["input"] == [
        {
            "type": "text",
            "text": "inspect this file",
            "text_elements": [],
        },
        {
            "type": "text",
            "text": (
                "Attached local file:\n"
                "name: recording.wav\n"
                "path: /tmp/recording.wav"
            ),
            "text_elements": [],
        },
    ]




def test_query_api_rejects_unsupported_attachment_kind(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    payload = base_request()
    payload["attachments"] = [
        {
            "kind": "document",
            "path": "/tmp/example.pdf",
        },
    ]

    response = client.post(
        "/v1/query",
        json=payload,
    )

    assert response.status_code == 422

    assert not any(
        method == "turn/start"
        for method, _ in codex.calls
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


def test_interrupt_turn(
    tmp_path,
):
    codex = FakeCodex()
    client = make_client(
        tmp_path,
        codex,
    )

    async def interrupt(
        method,
        params=None,
        **kwargs,
    ):
        codex.calls.append(
            (
                method,
                params or {},
            )
        )

        if method == "turn/interrupt":
            return {}

        raise AssertionError(
            f"unexpected method {method}"
        )

    codex.request = interrupt

    response = client.post(
        (
            "/v1/threads/"
            "thread-123/"
            "turns/turn-456/"
            "interrupt"
        )
    )

    assert response.status_code == 200

    assert response.json() == {
        "interrupted": True,
        "thread_id": "thread-123",
        "turn_id": "turn-456",
    }

    assert codex.calls == [
        (
            "turn/interrupt",
            {
                "threadId": "thread-123",
                "turnId": "turn-456",
            },
        ),
    ]


def test_interrupt_returns_503_without_codex(
    tmp_path,
):
    client = make_client(
        tmp_path,
        None,
    )

    response = client.post(
        (
            "/v1/threads/"
            "thread-123/"
            "turns/turn-456/"
            "interrupt"
        )
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"]["error"]
        == "codex_unavailable"
    )


def test_interaction_response_is_one_shot(
    tmp_path,
):
    from fastapi import FastAPI

    from chatgpt_query_broker.query_api import (
        create_query_router,
    )

    codex = FakeCodex()
    registry = InteractionRegistry()

    interaction_id = registry.register(
        request_id="server-request-1",
        conversation_id="thread-1",
        method=(
            "item/commandExecution/"
            "requestApproval"
        ),
    )

    backend = CodexQueryBackend(
        codex,
        interaction_registry=registry,
    )

    app = FastAPI()
    app.include_router(
        create_query_router(backend)
    )

    with TestClient(app) as client:
        response = client.post(
            (
                "/v1/interactions/"
                f"{interaction_id}/respond"
            ),
            json={
                "result": {
                    "decision": "accept",
                },
            },
        )

        assert response.status_code == 200

        assert response.json() == {
            "responded": True,
            "interaction_id": (
                interaction_id
            ),
            "thread_id": "thread-1",
            "method": (
                "item/commandExecution/"
                "requestApproval"
            ),
        }

        assert codex.responses == [
            {
                "request_id": (
                    "server-request-1"
                ),
                "result": {
                    "decision": "accept",
                },
                "error": None,
            },
        ]

        second = client.post(
            (
                "/v1/interactions/"
                f"{interaction_id}/respond"
            ),
            json={
                "result": {
                    "decision": "decline",
                },
            },
        )

        assert second.status_code == 404
        assert (
            second.json()["detail"][
                "error"
            ]
            == "interaction_not_found"
        )


def test_stream_relays_server_request_and_cleans_up():
    async def run():
        codex = FakeCodex()

        thread_id = "thread-interactive"
        turn_id = "turn-interactive"

        codex.subscribe_thread(
            thread_id
        )

        codex.queues[
            thread_id
        ].put_nowait(
            CodexServerRequest(
                request_id=77,
                method=(
                    "item/fileChange/"
                    "requestApproval"
                ),
                params={
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "item-1",
                    "reason": "test",
                    "startedAtMs": 1,
                },
            )
        )

        registry = InteractionRegistry()

        backend = CodexQueryBackend(
            codex,
            interaction_registry=registry,
        )

        stream = backend._stream_events(
            conversation_id=thread_id,
            execution_id=turn_id,
        )

        request_event = await anext(
            stream
        )

        assert (
            request_event["type"]
            == "server_request"
        )
        assert (
            request_event["method"]
            == (
                "item/fileChange/"
                "requestApproval"
            )
        )
        assert (
            request_event[
                "conversation_id"
            ]
            == thread_id
        )
        assert (
            request_event[
                "execution_id"
            ]
            == turn_id
        )

        interaction_id = (
            request_event[
                "interaction_id"
            ]
        )

        pending = registry.take(
            interaction_id
        )

        assert pending is not None
        assert pending.request_id == 77

        # Put it back so stream cleanup owns the
        # unanswered interaction.
        with registry._lock:
            registry._items[
                interaction_id
            ] = pending

        await stream.aclose()

        assert codex.responses == [
            {
                "request_id": 77,
                "result": None,
                "error": {
                    "code": -32000,
                    "message": (
                        "query stream closed "
                        "before interaction "
                        "response"
                    ),
                },
            },
        ]

        assert (
            thread_id
            not in codex.subscribed
        )

    asyncio.run(run())

def test_tools_enabled_uses_interactive_approval_policy(
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

    thread_start = next(
        params
        for method, params in codex.calls
        if method == "thread/start"
    )

    assert (
        thread_start["approvalPolicy"]
        == "on-request"
    )
    assert (
        thread_start["approvalsReviewer"]
        == "user"
    )


def test_tools_disabled_uses_never_approval_policy(
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

    assert (
        thread_start["approvalPolicy"]
        == "never"
    )
    assert (
        thread_start["approvalsReviewer"]
        == "user"
    )
