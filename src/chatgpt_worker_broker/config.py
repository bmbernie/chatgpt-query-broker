from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATABASE = (
    Path.home()
    / ".local"
    / "share"
    / "chatgpt-worker-broker"
    / "broker.sqlite3"
)


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8792
    database_path: Path = DEFAULT_DATABASE
    provider_url: str = "http://127.0.0.1:8791"
    provider_api_key: str = ""
    provider_timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "Settings":
        provider_api_key = os.getenv(
            "CHATGPT_WORKER_BROKER_PROVIDER_API_KEY",
            "",
        ).strip()

        if not provider_api_key:
            provider_api_key = os.getenv(
                "CHATGPT_WEB_API_KEY",
                "",
            ).strip()

        database = Path(
            os.getenv(
                "CHATGPT_WORKER_BROKER_DATABASE",
                str(DEFAULT_DATABASE),
            )
        ).expanduser()

        return cls(
            host=os.getenv(
                "CHATGPT_WORKER_BROKER_HOST",
                "127.0.0.1",
            ).strip()
            or "127.0.0.1",
            port=int(
                os.getenv(
                    "CHATGPT_WORKER_BROKER_PORT",
                    "8792",
                )
            ),
            database_path=database,
            provider_url=os.getenv(
                "CHATGPT_WORKER_BROKER_PROVIDER_URL",
                "http://127.0.0.1:8791",
            ).rstrip("/"),
            provider_api_key=provider_api_key,
            provider_timeout_seconds=float(
                os.getenv(
                    "CHATGPT_WORKER_BROKER_PROVIDER_TIMEOUT_SECONDS",
                    "180",
                )
            ),
        )

    def validate(self) -> None:
        if not self.host:
            raise ValueError(
                "CHATGPT_WORKER_BROKER_HOST must not be empty"
            )

        if not 1 <= self.port <= 65535:
            raise ValueError(
                "CHATGPT_WORKER_BROKER_PORT must be "
                "between 1 and 65535"
            )

        if not self.provider_url:
            raise ValueError(
                "provider URL must not be empty"
            )

        if not self.provider_api_key:
            raise ValueError(
                "provider API key is required; set "
                "CHATGPT_WORKER_BROKER_PROVIDER_API_KEY "
                "or CHATGPT_WEB_API_KEY"
            )

        if self.provider_timeout_seconds <= 0:
            raise ValueError(
                "provider timeout must be greater than zero"
            )
