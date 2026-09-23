from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable


class ToolsPolicy(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class QueryAttachment:
    kind: Literal["image", "audio", "file"]
    path: str


@dataclass(frozen=True, slots=True)
class QueryRequest:
    input: str
    cwd: str
    model: str
    reasoning_effort: str
    attachments: tuple[QueryAttachment, ...] = ()

    # Backend-neutral persistent conversation
    # identity. The HTTP compatibility layer can
    # continue exposing this as thread_id.
    conversation_id: str | None = None

    # Optional broker-level routing preference.
    # Concrete backends may ignore this field.
    backend: str | None = None

    tools: ToolsPolicy | None = None
    ephemeral: bool = True

    sandbox: Literal[
        "read-only",
        "workspace-write",
        "danger-full-access",
    ] = "read-only"


@dataclass(frozen=True, slots=True)
class QueryHandle:
    conversation_id: str
    execution_id: str
    events: AsyncIterator[dict[str, Any]]
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class QueryInteractionReceipt:
    interaction_id: str
    conversation_id: str
    method: str


@dataclass(frozen=True, slots=True)
class QueryBackendCapabilities:
    streaming: bool = True
    persistent_conversations: bool = False
    interruption: bool = False
    interactive_requests: bool = False
    tool_policy: bool = False
    sandbox: bool = False


class QueryBackendError(RuntimeError):
    pass


class QueryBackendUnavailable(QueryBackendError):
    pass


class QueryBackendBusy(QueryBackendError):
    def __init__(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ):
        self.conversation_id = conversation_id
        super().__init__(message)


class QueryBackendProtocolError(QueryBackendError):
    pass


class QueryBackendPolicyError(QueryBackendError):
    def __init__(
        self,
        *,
        error: str,
        message: str,
    ):
        self.error = error
        self.message = message
        super().__init__(message)


class QueryInteractionNotFound(QueryBackendError):
    pass


class QueryBackendRequestError(QueryBackendError):
    def __init__(
        self,
        *,
        code: int | str | None,
        message: str,
        data: Any = None,
    ):
        self.code = code
        self.message = message
        self.data = data

        super().__init__(message)


@runtime_checkable
class QueryBackend(Protocol):
    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities: ...

    async def start(
        self,
    ) -> dict[str, Any] | None: ...

    async def aclose(
        self,
    ) -> None: ...

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle: ...

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None: ...

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict[str, Any],
    ) -> QueryInteractionReceipt: ...
