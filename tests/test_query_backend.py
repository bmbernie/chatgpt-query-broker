from collections.abc import AsyncIterator

from chatgpt_query_broker.query_backend import (
    QueryBackend,
    QueryBackendCapabilities,
    QueryBackendRequestError,
    QueryHandle,
    QueryInteractionReceipt,
    QueryRequest,
    ToolsPolicy,
)


class FakeBackend:
    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities:
        return QueryBackendCapabilities(
            persistent_conversations=True,
            interruption=True,
        )

    async def start(self):
        return {
            "backend": "fake",
        }

    async def aclose(self):
        return None

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle:
        async def events(
        ) -> AsyncIterator[dict]:
            yield {
                "type": "completed",
            }

        return QueryHandle(
            conversation_id=(
                request.conversation_id
                or "conversation-1"
            ),
            execution_id="execution-1",
            events=events(),
        )

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None:
        return None

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict,
    ) -> QueryInteractionReceipt:
        return QueryInteractionReceipt(
            interaction_id=interaction_id,
            conversation_id="conversation-1",
            method="fake/request",
        )


def test_query_backend_protocol_accepts_fake():
    backend = FakeBackend()

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
        backend.capabilities.interruption
        is True
    )


def test_query_request_is_backend_neutral():
    request = QueryRequest(
        input="hello",
        cwd="/tmp",
        model="gpt-5.6-luna",
        reasoning_effort="xhigh",
        conversation_id="conversation-123",
        tools=ToolsPolicy.DISABLED,
    )

    assert (
        request.conversation_id
        == "conversation-123"
    )
    assert (
        request.tools
        == ToolsPolicy.DISABLED
    )
    assert request.sandbox == "read-only"


def test_query_backend_request_error_preserves_details():
    error = QueryBackendRequestError(
        code=-32000,
        message="backend failed",
        data={
            "reason": "test",
        },
    )

    assert error.code == -32000
    assert error.message == "backend failed"
    assert error.data == {
        "reason": "test",
    }
    assert str(error) == "backend failed"
