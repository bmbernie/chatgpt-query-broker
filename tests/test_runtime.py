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
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_WEB_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_WEB_BASE_URL",
        "http://127.0.0.1:9877",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_WEB_API_KEY",
        "test-web-key",
    )
    monkeypatch.setenv(
        "CHATGPT_QUERY_BROKER_WEB_REQUEST_TIMEOUT_SECONDS",
        "222",
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

    assert settings.web_enabled is True
    assert (
        settings.web_base_url
        == "http://127.0.0.1:9877"
    )
    assert (
        settings.web_api_key
        == "test-web-key"
    )
    assert (
        settings.web_request_timeout_seconds
        == 222.0
    )


def test_settings_environment_uses_codex_timeout_default(
    monkeypatch,
):
    monkeypatch.delenv(
        "CHATGPT_QUERY_BROKER_CODEX_REQUEST_TIMEOUT_SECONDS",
        raising=False,
    )

    direct = Settings()
    from_env = Settings.from_env()

    assert (
        from_env.codex_request_timeout_seconds
        == direct.codex_request_timeout_seconds
    )


def test_runtime_without_backend_is_healthy():
    settings = Settings()

    runtime = build_runtime(settings)

    assert runtime.query_backend is None
    assert runtime.codex is None
    assert runtime.web is None
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


class FakeWebBackend:
    def __init__(self):
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

        return {
            "ok": True,
            "backend": "browser",
        }

    async def aclose(self):
        self.closed = True


def test_runtime_registers_codex_and_web_backends():
    codex = FakeCodex()
    web = FakeWebBackend()

    runtime = build_runtime(
        Settings(),
        codex=codex,
        web_backend=web,
    )

    assert runtime.backend_registry.names == (
        "codex",
        "web",
    )
    assert (
        runtime.backend_registry.default_name
        == "codex"
    )

    assert (
        runtime.backend_registry.resolve(
            "web"
        )
        is web
    )

    with TestClient(runtime.app):
        assert codex.started is True
        assert web.started is True
        assert codex.closed is False
        assert web.closed is False

    assert codex.closed is True
    assert web.closed is True


def test_runtime_uses_web_as_default_when_it_is_only_backend():
    web = FakeWebBackend()

    runtime = build_runtime(
        Settings(),
        web_backend=web,
    )

    assert runtime.backend_registry.names == (
        "web",
    )
    assert (
        runtime.backend_registry.default_name
        == "web"
    )
    assert runtime.web is web

    with TestClient(runtime.app):
        assert web.started is True
        assert web.closed is False

    assert web.closed is True
