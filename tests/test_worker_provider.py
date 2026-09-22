from chatgpt_worker_broker.provider import (
    ProviderClient,
    ProviderProtocol,
)
from chatgpt_worker_broker.web_provider import (
    WebSessionProvider,
)
from chatgpt_worker_broker.worker_provider import (
    WorkerSessionProvider,
)


def test_legacy_provider_names_are_compatible():
    assert ProviderClient is WebSessionProvider
    assert ProviderProtocol is WorkerSessionProvider


def test_web_provider_satisfies_worker_contract():
    assert isinstance(
        WebSessionProvider,
        type,
    )

    required = {
        "create_session",
        "get_session",
        "list_sessions",
        "complete_session",
        "delete_session",
    }

    assert required <= set(
        dir(WebSessionProvider)
    )
