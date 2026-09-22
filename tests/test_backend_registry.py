import pytest

from chatgpt_query_broker.backend_registry import (
    BackendAlreadyRegistered,
    BackendNotFound,
    BackendRegistry,
    BackendRegistryError,
)


class FakeBackend:
    pass


def test_registry_resolves_default_backend():
    codex = FakeBackend()

    registry = BackendRegistry(
        {
            "codex": codex,
        },
        default="codex",
    )

    assert registry.names == (
        "codex",
    )
    assert registry.default_name == "codex"
    assert registry.resolve() is codex


def test_registry_resolves_explicit_backend():
    codex = FakeBackend()
    web = FakeBackend()

    registry = BackendRegistry(
        {
            "codex": codex,
            "web": web,
        },
        default="codex",
    )

    assert registry.resolve("codex") is codex
    assert registry.resolve("web") is web


def test_registry_rejects_duplicate_backend():
    registry = BackendRegistry()

    registry.register(
        "codex",
        FakeBackend(),
    )

    with pytest.raises(
        BackendAlreadyRegistered,
        match="already registered",
    ):
        registry.register(
            "codex",
            FakeBackend(),
        )


def test_registry_requires_known_default():
    registry = BackendRegistry()

    with pytest.raises(
        BackendNotFound,
        match="not registered",
    ):
        registry.set_default("codex")


def test_registry_requires_default_for_implicit_resolution():
    registry = BackendRegistry(
        {
            "codex": FakeBackend(),
        }
    )

    with pytest.raises(
        BackendNotFound,
        match="no default",
    ):
        registry.resolve()


def test_registry_rejects_invalid_backend_name():
    registry = BackendRegistry()

    with pytest.raises(
        BackendRegistryError,
        match="backend name",
    ):
        registry.register(
            "Codex App Server",
            FakeBackend(),
        )
