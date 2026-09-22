import asyncio

from chatgpt_worker_broker.codex_backend import (
    CodexQueryBackend,
    NO_TOOLS_CONFIG,
)
from chatgpt_worker_broker.codex_models import (
    CodexNotification,
    CodexServerRequest,
)
from chatgpt_worker_broker.query_backend import (
    QueryBackend,
    QueryBackendPolicyError,
    QueryInteractionNotFound,
    QueryRequest,
    ToolsPolicy,
)


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.queues = {}
        self.subscribed = set()
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True
        return {
            "userAgent": "fake-codex/0.1",
        }

    async def aclose(self):
        self.closed = True

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
            }

        if method == "thread/resume":
            return {
                "thread": {
                    "id": params["threadId"],
                },
            }

        if method == "turn/start":
            thread_id = params["threadId"]
            turn_id = f"turn-{thread_id}"

            self.queues[
                thread_id
            ].put_nowait(
                CodexNotification(
                    method=(
                        "item/agentMessage/delta"
                    ),
                    params={
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "item-1",
                        "delta": "hello",
                    },
                )
            )

            self.queues[
                thread_id
            ].put_nowait(
                CodexNotification(
                    method="turn/completed",
                    params={
                        "threadId": thread_id,
                        "turn": {
                            "id": turn_id,
                            "status": "completed",
                            "error": None,
                        },
                    },
                )
            )

            return {
                "turn": {
                    "id": turn_id,
                },
            }

        if method == "turn/interrupt":
            return {}

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

        self.subscribed.add(thread_id)
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
                "request_id": request_id,
                "result": result,
                "error": error,
            }
        )


def request(**overrides):
    values = {
        "input": "hello",
        "cwd": "/tmp",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "xhigh",
    }
    values.update(overrides)
    return QueryRequest(**values)


def test_codex_backend_satisfies_contract():
    backend = CodexQueryBackend(
        FakeCodex()
    )

    assert isinstance(
        backend,
        QueryBackend,
    )

    assert (
        backend.capabilities
        .persistent_conversations
        is True
    )
    assert (
        backend.capabilities
        .interactive_requests
        is True
    )


def test_codex_backend_starts_new_query():
    async def run():
        codex = FakeCodex()
        backend = CodexQueryBackend(
            codex
        )

        handle = await backend.start_query(
            request()
        )

        assert (
            handle.conversation_id
            == "thread-new"
        )
        assert (
            handle.execution_id
            == "turn-thread-new"
        )

        events = [
            event
            async for event in handle.events
        ]

        assert [
            event["type"]
            for event in events
        ] == [
            "delta",
            "completed",
        ]

        assert (
            events[0]["conversation_id"]
            == "thread-new"
        )
        assert (
            events[0]["execution_id"]
            == "turn-thread-new"
        )
        assert events[0]["delta"] == "hello"

        assert (
            "thread-new"
            not in codex.subscribed
        )

    asyncio.run(run())


def test_codex_backend_disables_tools():
    async def run():
        codex = FakeCodex()
        backend = CodexQueryBackend(
            codex
        )

        handle = await backend.start_query(
            request(
                tools=ToolsPolicy.DISABLED
            )
        )

        async for _ in handle.events:
            pass

        thread_start = next(
            params
            for method, params in codex.calls
            if method == "thread/start"
        )

        assert (
            thread_start["approvalPolicy"]
            == "never"
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

    asyncio.run(run())


def test_codex_backend_resumes_conversation():
    async def run():
        codex = FakeCodex()
        backend = CodexQueryBackend(
            codex
        )

        handle = await backend.start_query(
            request(
                conversation_id=(
                    "thread-existing"
                )
            )
        )

        assert (
            handle.conversation_id
            == "thread-existing"
        )

        async for _ in handle.events:
            pass

        resume = next(
            params
            for method, params in codex.calls
            if method == "thread/resume"
        )

        assert resume == {
            "threadId": "thread-existing",
            "excludeTurns": True,
        }

    asyncio.run(run())


def test_codex_backend_rejects_tool_change_on_resume():
    async def run():
        backend = CodexQueryBackend(
            FakeCodex()
        )

        try:
            await backend.start_query(
                request(
                    conversation_id=(
                        "thread-existing"
                    ),
                    tools=ToolsPolicy.DISABLED,
                )
            )
        except QueryBackendPolicyError as exc:
            assert (
                exc.error
                == "thread_tool_policy_is_fixed"
            )
        else:
            raise AssertionError(
                "tool policy change accepted"
            )

    asyncio.run(run())


def test_codex_backend_interrupts_execution():
    async def run():
        codex = FakeCodex()
        backend = CodexQueryBackend(
            codex
        )

        await backend.interrupt(
            "thread-1",
            "turn-1",
        )

        assert codex.calls == [
            (
                "turn/interrupt",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                },
            ),
        ]

    asyncio.run(run())


def test_codex_backend_interaction_round_trip():
    async def run():
        codex = FakeCodex()
        backend = CodexQueryBackend(
            codex
        )

        handle = await backend.start_query(
            request()
        )

        codex.queues[
            handle.conversation_id
        ]._queue.clear()

        codex.queues[
            handle.conversation_id
        ].put_nowait(
            CodexServerRequest(
                request_id=77,
                method=(
                    "item/fileChange/"
                    "requestApproval"
                ),
                params={
                    "threadId": (
                        handle.conversation_id
                    ),
                    "turnId": (
                        handle.execution_id
                    ),
                },
            )
        )

        event = await anext(
            handle.events
        )

        assert (
            event["type"]
            == "server_request"
        )

        receipt = (
            await backend.respond_interaction(
                event["interaction_id"],
                {
                    "decision": "accept",
                },
            )
        )

        assert (
            receipt.conversation_id
            == handle.conversation_id
        )

        assert codex.responses == [
            {
                "request_id": 77,
                "result": {
                    "decision": "accept",
                },
                "error": None,
            },
        ]

        try:
            await backend.respond_interaction(
                event["interaction_id"],
                {
                    "decision": "decline",
                },
            )
        except QueryInteractionNotFound:
            pass
        else:
            raise AssertionError(
                "interaction was not one-shot"
            )

        await handle.events.aclose()

    asyncio.run(run())
