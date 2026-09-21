"""Run the standalone Streamable HTTP MCP service."""

import logging

import uvicorn

from ctxttl_mcp.config import MCPSettings
from ctxttl_mcp.observability import configure_logging
from ctxttl_mcp.server import create_http_app


def main() -> None:
    settings = MCPSettings()
    configure_logging(settings.log_level)
    if settings.bearer_token is None:
        logging.getLogger("ctxttl_mcp").warning(
            "MCP bearer authentication is disabled",
            extra={
                "event": "authentication_disabled",
                "bind_host": settings.host,
            },
        )
    uvicorn.run(
        create_http_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
