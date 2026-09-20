from pathlib import Path

from fastapi.testclient import TestClient

from chatgpt_worker_broker.config import Settings
from chatgpt_worker_broker.provider import (
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)
from chatgpt_worker_broker.runtime import build_runtime


class FakeProvider:
    def __init__(self):
        self.sessions = {}
        self.list_calls = 0
        self.closed = False

    async def create_session(
        self,
        session_id,
        model,
        level,
    ):
        if session_id in self.sessions:
            raise ProviderSessionConflict(session_id)

        session = ProviderSession(
            session_id=session_id,
            model=model,
            level=level,
            state="ready",
        )
        self.sessions[session_id] = session
        return session

    async def get_session(self, session_id):
        try:
            return self.sessions[session_id]
        except KeyError:
            raise ProviderSessionNotFound(session_id) from None

    async def list_sessions(self):
        self.list_calls += 1
        return list(self.sessions.values())

    async def delete_session(self, session_id):
        if session_id not in self.sessions:
            raise ProviderSessionNotFound(session_id)

        del self.sessions[session_id]

    async def complete_session(
        self,
        session_id,
        messages,
    ):
        raise AssertionError(
            "completion should not be called in runtime tests"
        )

    async def aclose(self):
        self.closed = True


def test_settings_from_environment(monkeypatch, tmp_path):
    database = tmp_path / "runtime.sqlite3"

    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_HOST",
        "127.0.0.1",
    )
    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_PORT",
        "9876",
    )
    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_DATABASE",
        str(database),
    )
    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_PROVIDER_URL",
        "http://provider.test:1234",
    )
    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_PROVIDER_API_KEY",
        "test-key",
    )
    monkeypatch.setenv(
        "CHATGPT_WORKER_BROKER_PROVIDER_TIMEOUT_SECONDS",
        "321",
    )

    settings = Settings.from_env()

    assert settings.host == "127.0.0.1"
    assert settings.port == 9876
    assert settings.database_path == database
    assert settings.provider_url == "http://provider.test:1234"
    assert settings.provider_api_key == "test-key"
    assert settings.provider_timeout_seconds == 321.0


def test_provider_key_falls_back_to_existing_provider_env(
    monkeypatch,
):
    monkeypatch.delenv(
        "CHATGPT_WORKER_BROKER_PROVIDER_API_KEY",
        raising=False,
    )
    monkeypatch.setenv(
        "CHATGPT_WEB_API_KEY",
        "existing-provider-key",
    )

    settings = Settings.from_env()

    assert (
        settings.provider_api_key
        == "existing-provider-key"
    )


def test_runtime_seeds_catalog_reconciles_and_closes(
    tmp_path,
):
    fake = FakeProvider()

    settings = Settings(
        host="127.0.0.1",
        port=8792,
        database_path=tmp_path / "broker.sqlite3",
        provider_url="http://provider.test",
        provider_api_key="test-key",
        provider_timeout_seconds=120.0,
    )

    runtime = build_runtime(
        settings,
        provider=fake,
    )

    assert len(runtime.store.list_workers()) == 5

    with TestClient(runtime.app) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "workers": 5,
        }

        recovery = client.app.state.recovery

        assert recovery == {
            "workers_recovering": 0,
            "operations_indeterminate": 0,
            "workers_ready": 0,
            "workers_sleeping": 0,
            "workers_failed": 0,
        }

        assert fake.list_calls == 1
        assert fake.closed is False

    assert fake.closed is True
