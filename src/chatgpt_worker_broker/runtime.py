from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from .app import create_app
from .catalog import seed_default_workers
from .codex_backend import CodexQueryBackend
from .codex_client import CodexAppServerClient
from .config import Settings
from .service import BrokerService
from .store import BrokerStore
from .web_provider import WebSessionProvider


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: BrokerStore
    provider: Any
    query_backend: Any | None

    # Compatibility/debug visibility for the
    # underlying Codex transport.
    codex: Any | None

    service: BrokerService
    app: FastAPI


def build_runtime(
    settings: Settings,
    *,
    provider=None,
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

    store = BrokerStore(
        settings.database_path
    )

    seed_default_workers(store)

    actual_provider = (
        provider
        if provider is not None
        else WebSessionProvider(
            settings.provider_url,
            settings.provider_api_key,
            timeout=(
                settings.provider_timeout_seconds
            ),
        )
    )

    service = BrokerService(
        store,
        actual_provider,
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.query_backend = (
                actual_query_backend
            )
            app.state.query_backend_initialize = (
                None
            )

            # Keep these for existing diagnostics and
            # callers during the compatibility period.
            app.state.codex = actual_codex
            app.state.codex_initialize = None

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

            recovery = (
                await service.reconcile_after_restart()
            )

            app.state.recovery = recovery
            app.state.store = store
            app.state.service = service

            yield

        finally:
            try:
                if actual_query_backend is not None:
                    await actual_query_backend.aclose()

            finally:
                close = getattr(
                    actual_provider,
                    "aclose",
                    None,
                )

                if close is not None:
                    await close()

    app = create_app(
        store=store,
        service=service,
        query_backend=actual_query_backend,
        lifespan=lifespan,
    )

    return Runtime(
        settings=settings,
        store=store,
        provider=actual_provider,
        query_backend=actual_query_backend,
        codex=actual_codex,
        service=service,
        app=app,
    )
