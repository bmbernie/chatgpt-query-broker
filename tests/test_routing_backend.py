import pytest

from chatgpt_query_broker.backend_registry import (
    BackendRegistry,
)
from chatgpt_query_broker.query_backend import (
    QueryBackendCapabilities,
    QueryHandle,
    QueryInteractionReceipt,
    QueryRequest,
)
from chatgpt_query_broker.routing_backend import (
    RoutingQueryBackend,
)


async def empty_events():
    if False:
        yield {}


class FakeBackend:
    def __init__(
        self,
        name: str,
    ):
        self.name = name
        self.started = 0
        self.closed = 0
        self.queries = []
        self.interrupts = []
        self.interactions = []

        self._capabilities = (
            QueryBackendCapabilities(
                streaming=True,
                persistent_conversations=True,
                interruption=True,
                interactive_requests=True,
                tool_policy=True,
                sandbox=True,
            )
        )

    @property
    def capabilities(self):
        return self._capabilities

    async def start(self):
        self.started += 1

        return {
            "backend": self.name,
        }

    async def aclose(self):
        self.closed += 1

    async def start_query(
        self,
        request,
    ):
        self.queries.append(request)

        return QueryHandle(
            conversation_id=(
                f"{self.name}-conversation"
            ),
            execution_id=(
                f"{self.name}-execution"
            ),
            events=empty_events(),
        )

    async def interrupt(
        self,
        conversation_id,
        execution_id,
    ):
        self.interrupts.append(
            (
                conversation_id,
                execution_id,
            )
        )

    async def respond_interaction(
        self,
        interaction_id,
        result,
    ):
        self.interactions.append(
            (
                interaction_id,
                result,
            )
        )

        return QueryInteractionReceipt(
            interaction_id=interaction_id,
            conversation_id=(
                f"{self.name}-conversation"
            ),
            method="test/request",
        )


def make_request():
    return QueryRequest(
        input="hello",
        cwd="/tmp",
        model="gpt-test",
        reasoning_effort="high",
    )


def test_capabilities_follow_default_backend():
    codex = FakeBackend("codex")

    registry = BackendRegistry(
        {
            "codex": codex,
        },
        default="codex",
    )

    router = RoutingQueryBackend(
        registry
    )

    assert (
        router.capabilities
        is codex.capabilities
    )


@pytest.mark.asyncio
async def test_lifecycle_manages_registered_backends():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    registry = BackendRegistry(
        {
            "codex": codex,
            "web": web,
        },
        default="codex",
    )

    router = RoutingQueryBackend(
        registry
    )

    initialized = await router.start()

    assert initialized == {
        "backend": "codex",
    }
    assert codex.started == 1
    assert web.started == 1

    await router.aclose()

    assert codex.closed == 1
    assert web.closed == 1


@pytest.mark.asyncio
async def test_query_uses_default_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    registry = BackendRegistry(
        {
            "codex": codex,
            "web": web,
        },
        default="codex",
    )

    router = RoutingQueryBackend(
        registry
    )

    request = make_request()

    handle = await router.start_query(
        request
    )

    assert codex.queries == [
        request,
    ]
    assert web.queries == []
    conversation = decode_routed_id(
        handle.conversation_id
    )

    assert conversation is not None
    assert conversation.backend == "codex"
    assert (
        conversation.value
        == "codex-conversation"
    )


@pytest.mark.asyncio
async def test_control_operations_use_default_backend():
    codex = FakeBackend("codex")

    registry = BackendRegistry(
        {
            "codex": codex,
        },
        default="codex",
    )

    router = RoutingQueryBackend(
        registry
    )

    await router.interrupt(
        "conversation-1",
        "execution-1",
    )

    receipt = (
        await router.respond_interaction(
            "interaction-1",
            {
                "decision": "accept",
            },
        )
    )

    assert codex.interrupts == [
        (
            "conversation-1",
            "execution-1",
        ),
    ]

    assert codex.interactions == [
        (
            "interaction-1",
            {
                "decision": "accept",
            },
        ),
    ]

    interaction = decode_routed_id(
        receipt.interaction_id
    )

    assert interaction is not None
    assert interaction.backend == "codex"
    assert (
        interaction.value
        == "interaction-1"
    )


from dataclasses import replace

from chatgpt_query_broker.query_backend import (
    QueryBackendPolicyError,
)
from chatgpt_query_broker.routing_ids import (
    decode_routed_id,
    encode_routed_id,
)


@pytest.mark.asyncio
async def test_new_query_returns_scoped_ids():
    codex = FakeBackend("codex")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
            },
            default="codex",
        )
    )

    handle = await router.start_query(
        make_request()
    )

    conversation = decode_routed_id(
        handle.conversation_id
    )
    execution = decode_routed_id(
        handle.execution_id
    )

    assert conversation is not None
    assert conversation.backend == "codex"
    assert (
        conversation.value
        == "codex-conversation"
    )

    assert execution is not None
    assert execution.backend == "codex"
    assert (
        execution.value
        == "codex-execution"
    )


@pytest.mark.asyncio
async def test_scoped_resume_routes_to_owner():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    request = replace(
        make_request(),
        conversation_id=encode_routed_id(
            "web",
            "web-thread-1",
        ),
    )

    await router.start_query(request)

    assert codex.queries == []
    assert len(web.queries) == 1
    assert (
        web.queries[0].conversation_id
        == "web-thread-1"
    )


@pytest.mark.asyncio
async def test_legacy_resume_uses_default_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    request = replace(
        make_request(),
        conversation_id="legacy-thread",
    )

    await router.start_query(request)

    assert len(codex.queries) == 1
    assert (
        codex.queries[0].conversation_id
        == "legacy-thread"
    )
    assert web.queries == []


@pytest.mark.asyncio
async def test_interrupt_routes_to_scoped_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    await router.interrupt(
        encode_routed_id(
            "web",
            "web-thread",
        ),
        encode_routed_id(
            "web",
            "web-turn",
        ),
    )

    assert codex.interrupts == []
    assert web.interrupts == [
        (
            "web-thread",
            "web-turn",
        ),
    ]


@pytest.mark.asyncio
async def test_interrupt_rejects_backend_mismatch():
    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": FakeBackend(
                    "codex"
                ),
                "web": FakeBackend(
                    "web"
                ),
            },
            default="codex",
        )
    )

    with pytest.raises(
        QueryBackendPolicyError,
        match="belongs to backend",
    ):
        await router.interrupt(
            encode_routed_id(
                "codex",
                "thread-1",
            ),
            encode_routed_id(
                "web",
                "turn-1",
            ),
        )


@pytest.mark.asyncio
async def test_interaction_routes_to_scoped_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    interaction_id = encode_routed_id(
        "web",
        "interaction-1",
    )

    receipt = (
        await router.respond_interaction(
            interaction_id,
            {
                "decision": "accept",
            },
        )
    )

    assert codex.interactions == []
    assert web.interactions == [
        (
            "interaction-1",
            {
                "decision": "accept",
            },
        ),
    ]

    assert (
        receipt.interaction_id
        == interaction_id
    )

    conversation = decode_routed_id(
        receipt.conversation_id
    )

    assert conversation is not None
    assert conversation.backend == "web"


@pytest.mark.asyncio
async def test_stream_scopes_interaction_ids():
    class InteractiveBackend(
        FakeBackend
    ):
        async def start_query(
            self,
            request,
        ):
            self.queries.append(request)

            async def events():
                yield {
                    "type": "server_request",
                    "conversation_id": "thread-1",
                    "execution_id": "turn-1",
                    "interaction_id": "interaction-1",
                    "method": "test/request",
                    "params": {},
                }

            return QueryHandle(
                conversation_id="thread-1",
                execution_id="turn-1",
                events=events(),
            )

    codex = InteractiveBackend(
        "codex"
    )

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
            },
            default="codex",
        )
    )

    handle = await router.start_query(
        make_request()
    )

    event = await anext(
        handle.events
    )

    interaction = decode_routed_id(
        event["interaction_id"]
    )

    assert interaction is not None
    assert interaction.backend == "codex"
    assert (
        interaction.value
        == "interaction-1"
    )

    conversation = decode_routed_id(
        event["conversation_id"]
    )

    execution = decode_routed_id(
        event["execution_id"]
    )

    assert conversation is not None
    assert conversation.backend == "codex"

    assert execution is not None
    assert execution.backend == "codex"

    await handle.events.aclose()


@pytest.mark.asyncio
async def test_new_query_selects_requested_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    request = replace(
        make_request(),
        backend="web",
    )

    handle = await router.start_query(
        request
    )

    assert codex.queries == []
    assert len(web.queries) == 1

    # Routing metadata is consumed by the router.
    assert web.queries[0].backend is None

    conversation = decode_routed_id(
        handle.conversation_id
    )

    assert conversation is not None
    assert conversation.backend == "web"


@pytest.mark.asyncio
async def test_unknown_requested_backend_is_rejected():
    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": FakeBackend(
                    "codex"
                ),
            },
            default="codex",
        )
    )

    with pytest.raises(
        QueryBackendPolicyError,
    ) as exc_info:
        await router.start_query(
            replace(
                make_request(),
                backend="missing",
            )
        )

    assert (
        exc_info.value.error
        == "backend_not_found"
    )


@pytest.mark.asyncio
async def test_scoped_conversation_rejects_backend_conflict():
    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": FakeBackend(
                    "codex"
                ),
                "web": FakeBackend(
                    "web"
                ),
            },
            default="codex",
        )
    )

    with pytest.raises(
        QueryBackendPolicyError,
    ) as exc_info:
        await router.start_query(
            replace(
                make_request(),
                conversation_id=(
                    encode_routed_id(
                        "codex",
                        "thread-1",
                    )
                ),
                backend="web",
            )
        )

    assert (
        exc_info.value.error
        == "backend_conversation_mismatch"
    )


@pytest.mark.asyncio
async def test_legacy_conversation_can_select_backend():
    codex = FakeBackend("codex")
    web = FakeBackend("web")

    router = RoutingQueryBackend(
        BackendRegistry(
            {
                "codex": codex,
                "web": web,
            },
            default="codex",
        )
    )

    await router.start_query(
        replace(
            make_request(),
            conversation_id=(
                "legacy-web-thread"
            ),
            backend="web",
        )
    )

    assert codex.queries == []
    assert len(web.queries) == 1
    assert (
        web.queries[0].conversation_id
        == "legacy-web-thread"
    )
    assert web.queries[0].backend is None
