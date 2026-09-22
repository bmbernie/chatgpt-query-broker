from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from .app import create_app
from .backend_registry import BackendRegistry
from .codex_backend import CodexQueryBackend
from .codex_client import CodexAppServerClient
from .config import Settings
from .routing_backend import RoutingQueryBackend
from .web_backend import WebQueryBackend


@dataclass(slots=True)
class Runtime:
    settings: Settings
    backend_registry: BackendRegistry
    query_backend: Any | None

    # Compatibility/debug visibility for the
    # underlying transports/backends.
    codex: Any | None
    web: Any | None

    app: FastAPI


def build_runtime(
    settings: Settings,
    *,
    query_backend=None,
    codex=None,
    web_backend=None,
) -> Runtime:
    settings.validate()

    if (
        query_backend is not None
        and (
            codex is not None
            or web_backend is not None
        )
    ):
        raise ValueError(
            "provide query_backend or concrete "
            "backends, not both"
        )

    actual_codex = codex
    actual_web = web_backend
    generic_backend = query_backend
    codex_backend = None

    if generic_backend is None:
        if (
            actual_codex is None
            and settings.codex_enabled
        ):
            actual_codex = (
                CodexAppServerClient(
                    (
                        settings.codex_executable,
                        "app-server",
                    ),
                    request_timeout_seconds=(
                        settings
                        .codex_request_timeout_seconds
                    ),
                )
            )

        if actual_codex is not None:
            codex_backend = (
                CodexQueryBackend(
                    actual_codex
                )
            )

        if (
            actual_web is None
            and settings.web_enabled
        ):
            actual_web = WebQueryBackend(
                base_url=(
                    settings.web_base_url
                ),
                api_key=(
                    settings.web_api_key
                ),
                request_timeout_seconds=(
                    settings
                    .web_request_timeout_seconds
                ),
            )

    backend_registry = BackendRegistry()

    if generic_backend is not None:
        backend_registry.register(
            "default",
            generic_backend,
            default=True,
        )

    else:
        if codex_backend is not None:
            backend_registry.register(
                "codex",
                codex_backend,
                default=True,
            )

        if actual_web is not None:
            backend_registry.register(
                "web",
                actual_web,
                default=(
                    backend_registry.default_name
                    is None
                ),
            )

    routing_backend = (
        RoutingQueryBackend(
            backend_registry
        )
        if backend_registry.default_name
        is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.backend_registry = (
            backend_registry
        )
        app.state.query_backend = (
            routing_backend
        )
        app.state.query_backend_initialize = None

        # Keep Codex transport visibility during the
        # compatibility period.
        app.state.codex = actual_codex
        app.state.codex_initialize = None
        app.state.web = actual_web

        try:
            if routing_backend is not None:
                initialized = (
                    await routing_backend.start()
                )

                app.state.query_backend_initialize = (
                    initialized
                )

                if actual_codex is not None:
                    app.state.codex_initialize = (
                        initialized
                    )

            yield

        finally:
            if routing_backend is not None:
                await routing_backend.aclose()

    app = create_app(
        query_backend=routing_backend,
        lifespan=lifespan,
    )

    return Runtime(
        settings=settings,
        backend_registry=backend_registry,
        query_backend=routing_backend,
        codex=actual_codex,
        web=actual_web,
        app=app,
    )
