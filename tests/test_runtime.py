from fastapi.testclient import TestClient

from chatgpt_query_broker.config import Settings
from chatgpt_query_broker.runtime import build_runtime


class FakeCodex:
    def __init__(self):
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

        return {
            "userAgent": "fake-codex/0.1",
            "platformFamily": "unix",
        }

    async def aclose(self):
        self.closed = True


def test_settings_from_environment(
    monkeypatch,
):
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_HOST",
        "127.0.0.1",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_PORT",
        "9876",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_CODEX_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_CODEX_EXECUTABLE",
        "/opt/codex/bin/codex",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_CODEX_REQUEST_TIMEOUT_SECONDS",
        "45",
    )

    settings = Settings.from_env()

    assert settings.host == "127.0.0.1"
    assert settings.port == 9876
    assert settings.codex_enabled is True
    assert (
        settings.codex_executable
        == "/opt/codex/bin/codex"
    )
    assert (
        settings.codex_request_timeout_seconds
        == 45.0
    )


def test_runtime_without_backend_is_healthy():
    settings = Settings()

    runtime = build_runtime(settings)

    assert runtime.query_backend is None
    assert runtime.codex is None
    assert runtime.backend_registry.names == ()
    assert runtime.backend_registry.default_name is None

    with TestClient(runtime.app) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
        }

        assert client.app.state.query_backend is None
        assert client.app.state.codex is None
        assert (
            client.app.state.backend_registry
            is runtime.backend_registry
        )


def test_runtime_starts_and_closes_injected_codex():
    codex = FakeCodex()

    settings = Settings()

    runtime = build_runtime(
        settings,
        codex=codex,
    )

    assert runtime.codex is codex
    assert codex.started is False
    assert codex.closed is False

    assert runtime.backend_registry.names == (
        "codex",
    )
    assert (
        runtime.backend_registry.default_name
        == "codex"
    )
    assert (
        runtime.query_backend.registry
        is runtime.backend_registry
    )
    assert (
        runtime.backend_registry.resolve()
        is not runtime.query_backend
    )

    with TestClient(runtime.app) as client:
        assert codex.started is True
        assert codex.closed is False

        assert client.app.state.codex is codex
        assert client.app.state.codex_initialize == {
            "userAgent": "fake-codex/0.1",
            "platformFamily": "unix",
        }

        assert (
            client.app.state.query_backend
            is runtime.query_backend
        )

    assert codex.closed is True
