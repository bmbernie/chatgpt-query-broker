"""Compatibility imports for worker-session providers.

New code should import the neutral contract from
``worker_provider`` and the web implementation from
``web_provider``.
"""

from .web_provider import WebSessionProvider
from .worker_provider import (
    ProviderCompletion,
    ProviderError,
    ProviderRateLimitError,
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
    WorkerSessionProvider,
)


# Backward compatibility for existing callers.
ProviderClient = WebSessionProvider
ProviderProtocol = WorkerSessionProvider


__all__ = [
    "ProviderClient",
    "ProviderCompletion",
    "ProviderError",
    "ProviderProtocol",
    "ProviderRateLimitError",
    "ProviderSession",
    "ProviderSessionConflict",
    "ProviderSessionNotFound",
    "WebSessionProvider",
    "WorkerSessionProvider",
]
