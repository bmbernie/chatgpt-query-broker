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


@dataclass(slots=True)
class Runtime:
    settings: Settings
    backend_registry: BackendRegistry
    query_backend: Any | None

    # Compatibility/debug visibility for the
    # underlying Codex transport.
    codex: Any | None

    app: FastAPI


def build_runtime(
    settings: Settings,
    *,
    query_backend=None,
    codex=None,
) -> Runtime:
    settings.validate()

    if (
        query_backend is not None
        and codex is not None
    ):
        raise ValueError(
            "provide query_backend or codex, "
            "not both"
        )

    actual_codex = codex
    actual_query_backend = query_backend

    if actual_query_backend is None:
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
            actual_query_backend = (
                CodexQueryBackend(
                    actual_codex
                )
            )

    backend_registry = BackendRegistry()

    if actual_query_backend is not None:
        backend_name = (
            "codex"
            if actual_codex is not None
            else "default"
        )

        backend_registry.register(
            backend_name,
            actual_query_backend,
            default=True,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.backend_registry = (
            backend_registry
        )
        app.state.query_backend = (
            actual_query_backend
        )
        app.state.query_backend_initialize = None

        # Keep Codex transport visibility during the
        # compatibility period.
        app.state.codex = actual_codex
        app.state.codex_initialize = None

        try:
            if actual_query_backend is not None:
                initialized = (
                    await actual_query_backend.start()
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
            if actual_query_backend is not None:
                await actual_query_backend.aclose()

    app = create_app(
        query_backend=actual_query_backend,
        lifespan=lifespan,
    )

    return Runtime(
        settings=settings,
        backend_registry=backend_registry,
        query_backend=actual_query_backend,
        codex=actual_codex,
        app=app,
    )
