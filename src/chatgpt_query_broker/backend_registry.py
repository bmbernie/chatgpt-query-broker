from __future__ import annotations

import re
from collections.abc import Mapping

from .query_backend import QueryBackend


_BACKEND_NAME = re.compile(
    r"^[a-z][a-z0-9_-]{0,63}$"
)


class BackendRegistryError(ValueError):
    pass


class BackendNotFound(BackendRegistryError):
    pass


class BackendAlreadyRegistered(
    BackendRegistryError
):
    pass


class BackendRegistry:
    def __init__(
        self,
        backends: Mapping[
            str,
            QueryBackend,
        ] | None = None,
        *,
        default: str | None = None,
    ):
        self._backends: dict[
            str,
            QueryBackend,
        ] = {}
        self._default_name: str | None = None

        if backends is not None:
            for name, backend in backends.items():
                self.register(
                    name,
                    backend,
                )

        if default is not None:
            self.set_default(default)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._backends)

    @property
    def default_name(self) -> str | None:
        return self._default_name

    def register(
        self,
        name: str,
        backend: QueryBackend,
        *,
        default: bool = False,
    ) -> None:
        if not _BACKEND_NAME.fullmatch(name):
            raise BackendRegistryError(
                "backend name must match "
                "[a-z][a-z0-9_-]{0,63}"
            )

        if name in self._backends:
            raise BackendAlreadyRegistered(
                f"backend already registered: {name}"
            )

        self._backends[name] = backend

        if default:
            self._default_name = name

    def set_default(
        self,
        name: str,
    ) -> None:
        if name not in self._backends:
            raise BackendNotFound(
                f"backend not registered: {name}"
            )

        self._default_name = name

    def resolve(
        self,
        name: str | None = None,
    ) -> QueryBackend:
        selected = (
            name
            if name is not None
            else self._default_name
        )

        if selected is None:
            raise BackendNotFound(
                "no default backend configured"
            )

        try:
            return self._backends[selected]
        except KeyError:
            raise BackendNotFound(
                f"backend not registered: {selected}"
            ) from None
