from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderSession:
    session_id: str
    model: str
    level: str
    state: str
    conversation_policy: str = "regular"


@dataclass(frozen=True, slots=True)
class ProviderCompletion:
    response_id: str
    session_id: str
    model: str
    level: str
    content: str


class ProviderError(RuntimeError):
    pass


class ProviderRateLimitError(ProviderError):
    def __init__(
        self,
        retry_after: str | None = None,
    ):
        self.retry_after = retry_after

        super().__init__(
            "provider is temporarily rate limited"
        )


class ProviderSessionNotFound(ProviderError):
    pass


class ProviderSessionConflict(ProviderError):
    pass


class WorkerSessionProvider(Protocol):
    async def create_session(
        self,
        session_id: str,
        model: str,
        level: str,
        conversation_policy: str = "regular",
    ) -> ProviderSession: ...

    async def get_session(
        self,
        session_id: str,
    ) -> ProviderSession: ...

    async def list_sessions(
        self,
    ) -> list[ProviderSession]: ...

    async def complete_session(
        self,
        session_id: str,
        messages: list[dict],
    ) -> ProviderCompletion: ...

    async def delete_session(
        self,
        session_id: str,
    ) -> None: ...
