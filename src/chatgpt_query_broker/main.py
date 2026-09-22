from __future__ import annotations

import os
import socket

import uvicorn

from .config import Settings
from .runtime import build_runtime


def _systemd_notify(
    message: str,
) -> None:
    notify_socket = os.environ.get(
        "NOTIFY_SOCKET"
    )

    if not notify_socket:
        return

    address = (
        "\0" + notify_socket[1:]
        if notify_socket.startswith("@")
        else notify_socket
    )

    with socket.socket(
        socket.AF_UNIX,
        socket.SOCK_DGRAM,
    ) as notifier:
        notifier.sendto(
            message.encode("utf-8"),
            address,
        )


class SystemdReadyServer(
    uvicorn.Server
):
    async def startup(
        self,
        sockets=None,
    ) -> None:
        await super().startup(
            sockets=sockets
        )

        # Uvicorn sets started only after:
        # 1. FastAPI lifespan startup completed;
        # 2. backend startup completed;
        # 3. the HTTP listener was bound.
        if self.started:
            _systemd_notify(
                "READY=1"
            )


def main() -> None:
    settings = Settings.from_env()
    runtime = build_runtime(settings)

    config = uvicorn.Config(
        runtime.app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )

    server = SystemdReadyServer(
        config
    )

    server.run()


if __name__ == "__main__":
    main()
