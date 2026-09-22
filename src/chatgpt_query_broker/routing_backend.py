from __future__ import annotations

from typing import Any

from .backend_registry import BackendRegistry
from .query_backend import (
    QueryBackendCapabilities,
    QueryHandle,
    QueryInteractionReceipt,
    QueryRequest,
)


class RoutingQueryBackend:
    def __init__(
        self,
        registry: BackendRegistry,
    ):
        self.registry = registry

    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities:
        return self.registry.resolve().capabilities

    async def start(
        self,
    ) -> dict[str, Any] | None:
        results: dict[int, dict[str, Any] | None] = {}
        started: set[int] = set()

        for name in self.registry.names:
            backend = self.registry.resolve(name)
            identity = id(backend)

            if identity in started:
                continue

            started.add(identity)
            results[identity] = await backend.start()

        default_backend = self.registry.resolve()

        return results[id(default_backend)]

    async def aclose(
        self,
    ) -> None:
        closed: set[int] = set()

        for name in reversed(
            self.registry.names
        ):
            backend = self.registry.resolve(name)
            identity = id(backend)

            if identity in closed:
                continue

            closed.add(identity)
            await backend.aclose()

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle:
        backend = self.registry.resolve()

        return await backend.start_query(
            request
        )

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None:
        backend = self.registry.resolve()

        await backend.interrupt(
            conversation_id,
            execution_id,
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict[str, Any],
    ) -> QueryInteractionReceipt:
        backend = self.registry.resolve()

        return await backend.respond_interaction(
            interaction_id,
            result,
        )
