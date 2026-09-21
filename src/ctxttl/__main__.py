"""Command-line entry point for the local CtxTTL proxy."""

import uvicorn

from ctxttl.api import create_app
from ctxttl.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
