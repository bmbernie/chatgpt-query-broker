from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from .app import create_app
from .catalog import seed_default_workers
from .codex_client import CodexAppServerClient
from .config import Settings
from .provider import ProviderClient
from .service import BrokerService
from .store import BrokerStore


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: BrokerStore
    provider: Any
    codex: Any | None
    service: BrokerService
    app: FastAPI


def build_runtime(
    settings: Settings,
    *,
    provider=None,
    codex=None,
) -> Runtime:
    settings.validate()

    store = BrokerStore(
        settings.database_path
    )

    seed_default_workers(store)

    actual_provider = (
        provider
        if provider is not None
        else ProviderClient(
            settings.provider_url,
            settings.provider_api_key,
            timeout=settings.provider_timeout_seconds,
        )
    )

    service = BrokerService(
        store,
        actual_provider,
    )

    actual_codex = codex

    if (
        actual_codex is None
        and settings.codex_enabled
    ):
        actual_codex = CodexAppServerClient(
            (
                settings.codex_executable,
                "app-server",
            ),
            request_timeout_seconds=(
                settings.codex_request_timeout_seconds
            ),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.codex = actual_codex
            app.state.codex_initialize = None

            if actual_codex is not None:
                app.state.codex_initialize = (
                    await actual_codex.start()
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
                if actual_codex is not None:
                    await actual_codex.aclose()

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
        codex=actual_codex,
        lifespan=lifespan,
    )

    return Runtime(
        settings=settings,
        store=store,
        provider=actual_provider,
        codex=actual_codex,
        service=service,
        app=app,
    )
