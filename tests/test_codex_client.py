import asyncio
import sys
from pathlib import Path

import pytest

from chatgpt_query_broker.codex_client import (
    CodexAppServerClient,
)
from chatgpt_query_broker.codex_models import (
    CodexNotification,
    CodexProcessExited,
    CodexRequestError,
    CodexServerRequest,
)


FAKE_SERVER = r'''
import json
import sys


def send(message):
    sys.stdout.write(
        json.dumps(
            message,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()


delayed = []
server_request_parent = None


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")

    if method == "initialize":
        send(
            {
                "id": message["id"],
                "result": {
                    "userAgent": "fake-codex/0.1",
                    "platformFamily": "unix",
                },
            }
        )
        continue

    if method == "notifications/initialized":
        send(
            {
                "method": "server/ready",
                "params": {
                    "ok": True,
                },
            }
        )
        continue

    if method == "echo":
        send(
            {
                "method": "test/event",
                "params": {
                    "value": (
                        message["params"]["value"]
                    ),
                },
            }
        )

        send(
            {
                "id": message["id"],
                "result": {
                    "value": (
                        message["params"]["value"]
                    ),
                },
            }
        )

        continue

    if method == "fail":
        send(
            {
                "id": message["id"],
                "error": {
                    "code": 42901,
                    "message": "rate limited",
                    "data": {
                        "retryAfter": "17",
                    },
                },
            }
        )

        continue

    if method == "delayed":
        delayed.append(message)

        if len(delayed) == 2:
            second = delayed[1]
            first = delayed[0]

            send(
                {
                    "id": second["id"],
                    "result": {
                        "value": (
                            second["params"]["value"]
                        ),
                    },
                }
            )

            send(
                {
                    "id": first["id"],
                    "result": {
                        "value": (
                            first["params"]["value"]
                        ),
                    },
                }
            )

        continue

    if method == "trigger/request":
        server_request_parent = message["id"]

        params = {
            "kind": "test",
        }

        thread_id = (
            message.get("params") or {}
        ).get("threadId")

        if thread_id is not None:
            params["threadId"] = thread_id
            params["turnId"] = "turn-approval"

        send(
            {
                "id": "srv-1",
                "method": "approval/request",
                "params": params,
            }
        )

        continue

    if method == "emit/two-threads":
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-b",
                    "turnId": "turn-b",
                    "itemId": "item-b",
                    "delta": "B",
                },
            }
        )

        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-a",
                    "turnId": "turn-a",
                    "itemId": "item-a",
                    "delta": "A",
                },
            }
        )

        send(
            {
                "id": message["id"],
                "result": {
                    "ok": True,
                },
            }
        )

        continue

    if method == "emit/large-line":
        send(
            {
                "method": "test/large-event",
                "params": {
                    "value": "x" * (128 * 1024),
                },
            }
        )

        send(
            {
                "id": message["id"],
                "result": {
                    "ok": True,
                },
            }
        )

        continue

    if (
        message.get("id") == "srv-1"
        and method is None
    ):
        send(
            {
                "id": server_request_parent,
                "result": {
                    "approval": (
                        message.get("result")
                    ),
                },
            }
        )

        continue

    if method == "exit":
        sys.exit(0)
'''


def write_fake_server(
    tmp_path: Path,
) -> Path:
    path = (
        tmp_path
        / "fake_codex_server.py"
    )

    path.write_text(FAKE_SERVER)

    return path


def make_client(
    tmp_path: Path,
) -> CodexAppServerClient:
    server = write_fake_server(
        tmp_path
    )

    return CodexAppServerClient(
        (
            sys.executable,
            "-u",
            str(server),
        ),
        request_timeout_seconds=2.0,
    )


def test_start_request_notifications_and_close(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        init = await client.start()

        assert client.running is True
        assert (
            init["userAgent"]
            == "fake-codex/0.1"
        )

        ready = await client.next_message(
            timeout=1.0
        )

        assert ready == CodexNotification(
            method="server/ready",
            params={"ok": True},
        )

        response = await client.request(
            "echo",
            {"value": "hello"},
        )

        assert response == {
            "value": "hello",
        }

        event = await client.next_message(
            timeout=1.0
        )

        assert event == CodexNotification(
            method="test/event",
            params={"value": "hello"},
        )

        await client.aclose()

        assert client.running is False

    asyncio.run(run())


def test_concurrent_requests_are_correlated_by_id(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        first, second = (
            await asyncio.gather(
                client.request(
                    "delayed",
                    {"value": "first"},
                ),
                client.request(
                    "delayed",
                    {"value": "second"},
                ),
            )
        )

        assert first == {
            "value": "first",
        }

        assert second == {
            "value": "second",
        }

        await client.aclose()

    asyncio.run(run())


def test_request_error_is_typed(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        with pytest.raises(
            CodexRequestError
        ) as caught:
            await client.request(
                "fail",
                {},
            )

        assert caught.value.code == 42901
        assert (
            caught.value.message
            == "rate limited"
        )
        assert caught.value.data == {
            "retryAfter": "17",
        }

        await client.aclose()

    asyncio.run(run())


def test_server_requests_can_be_answered(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        pending = asyncio.create_task(
            client.request(
                "trigger/request",
                {},
            )
        )

        request = await client.next_message(
            timeout=1.0
        )

        assert request == CodexServerRequest(
            request_id="srv-1",
            method="approval/request",
            params={"kind": "test"},
        )

        await client.respond(
            request.request_id,
            result={
                "decision": "deny",
            },
        )

        result = await pending

        assert result == {
            "approval": {
                "decision": "deny",
            },
        }

        await client.aclose()

    asyncio.run(run())


def test_process_exit_fails_pending_request(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        with pytest.raises(
            CodexProcessExited
        ):
            await client.request(
                "exit",
                {},
            )

        await client.aclose()

    asyncio.run(run())



def test_thread_subscriptions_demultiplex_events(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        client.subscribe_thread(
            "thread-a"
        )
        client.subscribe_thread(
            "thread-b"
        )

        result = await client.request(
            "emit/two-threads",
            {},
        )

        assert result == {
            "ok": True,
        }

        event_a = (
            await client.next_thread_message(
                "thread-a",
                timeout=1.0,
            )
        )

        event_b = (
            await client.next_thread_message(
                "thread-b",
                timeout=1.0,
            )
        )

        assert event_a == CodexNotification(
            method="item/agentMessage/delta",
            params={
                "threadId": "thread-a",
                "turnId": "turn-a",
                "itemId": "item-a",
                "delta": "A",
            },
        )

        assert event_b == CodexNotification(
            method="item/agentMessage/delta",
            params={
                "threadId": "thread-b",
                "turnId": "turn-b",
                "itemId": "item-b",
                "delta": "B",
            },
        )

        with pytest.raises(
            asyncio.TimeoutError
        ):
            await client.next_message(
                timeout=0.05
            )

        client.unsubscribe_thread(
            "thread-a"
        )
        client.unsubscribe_thread(
            "thread-b"
        )

        await client.aclose()

    asyncio.run(run())


def test_large_jsonl_message_is_supported(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        result = await client.request(
            "emit/large-line",
            {},
        )

        assert result == {
            "ok": True,
        }

        event = await client.next_message(
            timeout=1.0
        )

        assert event == CodexNotification(
            method="test/large-event",
            params={
                "value": "x" * (128 * 1024),
            },
        )

        await client.aclose()

    asyncio.run(run())


def test_thread_subscription_routes_server_requests(
    tmp_path,
):
    async def run():
        client = make_client(tmp_path)

        await client.start()
        await client.next_message(
            timeout=1.0
        )

        client.subscribe_thread(
            "thread-tools"
        )

        pending = asyncio.create_task(
            client.request(
                "trigger/request",
                {
                    "threadId": "thread-tools",
                },
            )
        )

        request = (
            await client.next_thread_message(
                "thread-tools",
                timeout=1.0,
            )
        )

        assert request == CodexServerRequest(
            request_id="srv-1",
            method="approval/request",
            params={
                "kind": "test",
                "threadId": "thread-tools",
                "turnId": "turn-approval",
            },
        )

        await client.respond(
            request.request_id,
            result={
                "decision": "deny",
            },
        )

        result = await pending

        assert result == {
            "approval": {
                "decision": "deny",
            },
        }

        client.unsubscribe_thread(
            "thread-tools"
        )

        await client.aclose()

    asyncio.run(run())
