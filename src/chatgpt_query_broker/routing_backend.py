from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from .backend_registry import (
    BackendNotFound,
    BackendRegistry,
)
from .query_backend import (
    QueryBackendBusy,
    QueryBackendCapabilities,
    QueryBackendPolicyError,
    QueryBackendUnavailable,
    QueryHandle,
    QueryInteractionReceipt,
    QueryRequest,
)
from .routing_ids import (
    RoutedIdError,
    decode_routed_id,
    encode_routed_id,
)


class RoutingQueryBackend:
    def __init__(
        self,
        registry: BackendRegistry,
    ):
        self.registry = registry

    def _default_name(self) -> str:
        name = self.registry.default_name

        if name is None:
            raise QueryBackendUnavailable(
                "no default backend configured"
            )

        return name

    def _resolve(
        self,
        name: str,
    ):
        try:
            return self.registry.resolve(name)
        except BackendNotFound as exc:
            raise QueryBackendUnavailable(
                str(exc)
            ) from exc

    def _decode_id(
        self,
        value: str,
    ):
        try:
            return decode_routed_id(value)
        except RoutedIdError as exc:
            raise QueryBackendPolicyError(
                error="invalid_routed_id",
                message=str(exc),
            ) from exc

    def _route_id(
        self,
        value: str,
    ) -> tuple[str, str]:
        routed = self._decode_id(value)

        if routed is None:
            return (
                self._default_name(),
                value,
            )

        return (
            routed.backend,
            routed.value,
        )

    def _owned_id(
        self,
        *,
        backend_name: str,
        value: str,
        kind: str,
    ) -> str:
        routed = self._decode_id(value)

        if routed is None:
            return value

        if routed.backend != backend_name:
            raise QueryBackendPolicyError(
                error="backend_id_mismatch",
                message=(
                    f"{kind} belongs to backend "
                    f"{routed.backend!r}, not "
                    f"{backend_name!r}"
                ),
            )

        return routed.value

    @property
    def capabilities(
        self,
    ) -> QueryBackendCapabilities:
        backend = self._resolve(
            self._default_name()
        )

        return backend.capabilities

    async def start(
        self,
    ) -> dict[str, Any] | None:
        results: dict[
            int,
            dict[str, Any] | None,
        ] = {}
        started: set[int] = set()

        for name in self.registry.names:
            backend = self._resolve(name)
            identity = id(backend)

            if identity in started:
                continue

            started.add(identity)
            results[identity] = await backend.start()

        default_backend = self._resolve(
            self._default_name()
        )

        return results[id(default_backend)]

    async def aclose(
        self,
    ) -> None:
        closed: set[int] = set()

        for name in reversed(
            self.registry.names
        ):
            backend = self._resolve(name)
            identity = id(backend)

            if identity in closed:
                continue

            closed.add(identity)
            await backend.aclose()

    async def _events(
        self,
        *,
        backend_name: str,
        events: AsyncIterator[
            dict[str, Any]
        ],
    ):
        try:
            async for event in events:
                value = dict(event)

                conversation_id = value.get(
                    "conversation_id"
                )

                if (
                    isinstance(
                        conversation_id,
                        str,
                    )
                    and conversation_id
                ):
                    value[
                        "conversation_id"
                    ] = encode_routed_id(
                        backend_name,
                        conversation_id,
                    )

                execution_id = value.get(
                    "execution_id"
                )

                if (
                    isinstance(
                        execution_id,
                        str,
                    )
                    and execution_id
                ):
                    value[
                        "execution_id"
                    ] = encode_routed_id(
                        backend_name,
                        execution_id,
                    )

                interaction_id = value.get(
                    "interaction_id"
                )

                if (
                    isinstance(
                        interaction_id,
                        str,
                    )
                    and interaction_id
                ):
                    value[
                        "interaction_id"
                    ] = encode_routed_id(
                        backend_name,
                        interaction_id,
                    )

                yield value

        finally:
            close = getattr(
                events,
                "aclose",
                None,
            )

            if close is not None:
                await close()

    async def start_query(
        self,
        request: QueryRequest,
    ) -> QueryHandle:
        if request.conversation_id is None:
            backend_name = self._default_name()
            backend_request = request

        else:
            (
                backend_name,
                conversation_id,
            ) = self._route_id(
                request.conversation_id
            )

            backend_request = replace(
                request,
                conversation_id=conversation_id,
            )

        backend = self._resolve(
            backend_name
        )

        try:
            handle = await backend.start_query(
                backend_request
            )

        except QueryBackendBusy as exc:
            conversation_id = (
                encode_routed_id(
                    backend_name,
                    exc.conversation_id,
                )
                if exc.conversation_id
                else None
            )

            raise QueryBackendBusy(
                str(exc),
                conversation_id=(
                    conversation_id
                ),
            ) from exc

        return QueryHandle(
            conversation_id=encode_routed_id(
                backend_name,
                handle.conversation_id,
            ),
            execution_id=encode_routed_id(
                backend_name,
                handle.execution_id,
            ),
            events=self._events(
                backend_name=backend_name,
                events=handle.events,
            ),
        )

    async def interrupt(
        self,
        conversation_id: str,
        execution_id: str,
    ) -> None:
        (
            backend_name,
            raw_conversation_id,
        ) = self._route_id(
            conversation_id
        )

        raw_execution_id = self._owned_id(
            backend_name=backend_name,
            value=execution_id,
            kind="execution id",
        )

        backend = self._resolve(
            backend_name
        )

        await backend.interrupt(
            raw_conversation_id,
            raw_execution_id,
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        result: dict[str, Any],
    ) -> QueryInteractionReceipt:
        (
            backend_name,
            raw_interaction_id,
        ) = self._route_id(
            interaction_id
        )

        backend = self._resolve(
            backend_name
        )

        receipt = (
            await backend.respond_interaction(
                raw_interaction_id,
                result,
            )
        )

        return QueryInteractionReceipt(
            interaction_id=encode_routed_id(
                backend_name,
                receipt.interaction_id,
            ),
            conversation_id=encode_routed_id(
                backend_name,
                receipt.conversation_id,
            ),
            method=receipt.method,
        )
