from __future__ import annotations

from fastapi import FastAPI

from .query_api import create_query_router
from .query_backend import QueryBackend


def create_app(
    *,
    query_backend: QueryBackend | None = None,
    lifespan=None,
) -> FastAPI:
    app = FastAPI(
        title="ChatGPT Query Broker",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(
        create_query_router(query_backend)
    )

    @app.get("/health")
    async def health():
        return {
            "ok": True,
        }

    return app
