from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(
    name: str,
    default: bool,
) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    value = raw.strip().lower()

    if value in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if value in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise ValueError(
        f"{name} must be a boolean value"
    )


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8792

    codex_enabled: bool = False
    codex_executable: str = "codex"
    codex_request_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv(
                "CHATGPT_QUERY_BROKER_HOST",
                "127.0.0.1",
            ).strip()
            or "127.0.0.1",
            port=int(
                os.getenv(
                    "CHATGPT_QUERY_BROKER_PORT",
                    "8792",
                )
            ),
            codex_enabled=_env_bool(
                "CHATGPT_QUERY_BROKER_CODEX_ENABLED",
                False,
            ),
            codex_executable=os.getenv(
                "CHATGPT_QUERY_BROKER_CODEX_EXECUTABLE",
                "codex",
            ).strip(),
            codex_request_timeout_seconds=float(
                os.getenv(
                    "CHATGPT_QUERY_BROKER_CODEX_REQUEST_TIMEOUT_SECONDS",
                    "60",
                )
            ),
        )

    def validate(self) -> None:
        if not self.host:
            raise ValueError(
                "CHATGPT_QUERY_BROKER_HOST must not be empty"
            )

        if not 1 <= self.port <= 65535:
            raise ValueError(
                "CHATGPT_QUERY_BROKER_PORT must be "
                "between 1 and 65535"
            )
