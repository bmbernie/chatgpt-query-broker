from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from .app import create_app
from .catalog import seed_default_workers
from .config import Settings
from .provider import ProviderClient
from .service import BrokerService
from .store import BrokerStore


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: BrokerStore
    provider: Any
    service: BrokerService
    app: FastAPI


def build_runtime(
    settings: Settings,
    *,
    provider=None,
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            recovery = (
                await service.reconcile_after_restart()
            )

            app.state.recovery = recovery
            app.state.store = store
            app.state.service = service

            yield

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
        lifespan=lifespan,
    )

    return Runtime(
        settings=settings,
        store=store,
        provider=actual_provider,
        service=service,
        app=app,
    )
