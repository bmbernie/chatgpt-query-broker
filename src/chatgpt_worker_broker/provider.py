from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class ProviderSession:
    session_id: str
    model: str
    level: str
    state: str


class ProviderError(RuntimeError):
    pass


class ProviderSessionNotFound(ProviderError):
    pass


class ProviderSessionConflict(ProviderError):
    pass


class ProviderClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _session(data: dict) -> ProviderSession:
        return ProviderSession(
            session_id=data["session_id"],
            model=data["model"],
            level=data["level"],
            state=data["state"],
        )

    async def create_session(
        self,
        session_id: str,
        model: str,
        level: str,
    ) -> ProviderSession:
        response = await self._client.post(
            "/v1/sessions",
            json={
                "session_id": session_id,
                "model": model,
                "reasoning_effort": level,
            },
        )

        if response.status_code == 409:
            raise ProviderSessionConflict(session_id)

        response.raise_for_status()
        return self._session(response.json())

    async def get_session(
        self,
        session_id: str,
    ) -> ProviderSession:
        response = await self._client.get(
            f"/v1/sessions/{session_id}"
        )

        if response.status_code == 404:
            raise ProviderSessionNotFound(session_id)

        response.raise_for_status()
        return self._session(response.json())

    async def list_sessions(self) -> list[ProviderSession]:
        response = await self._client.get("/v1/sessions")
        response.raise_for_status()

        return [
            self._session(item)
            for item in response.json()["data"]
        ]

    async def delete_session(
        self,
        session_id: str,
    ) -> None:
        response = await self._client.delete(
            f"/v1/sessions/{session_id}"
        )

        if response.status_code == 404:
            raise ProviderSessionNotFound(session_id)

        response.raise_for_status()
