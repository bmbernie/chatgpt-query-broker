from __future__ import annotations

import uvicorn

from .config import Settings
from .runtime import build_runtime


def main() -> None:
    settings = Settings.from_env()
    runtime = build_runtime(settings)

    uvicorn.run(
        runtime.app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
