"""Run the Local Code MCP server.

    LOCAL_CODE_REPO_ROOT=/repos/target python -m mcp_servers.local_code                 # stdio
    LOCAL_CODE_REPO_ROOT=/repos/target MCP_AUTH_TOKEN=... \\
        python -m mcp_servers.local_code --transport streamable-http --port 9101           # HTTP, bearer token

Logs go to stderr: on stdio, stdout carries the protocol.
"""

import argparse
import logging
import sys

from mcp_common.auth import load_token, protected_http_app
from mcp_servers.local_code.settings import LocalCodeSettings
from mcp_servers.local_code.tools import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mcp_servers.local_code")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9101)
    args = parser.parse_args()

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = build_server(LocalCodeSettings.from_env())

    if args.transport == "stdio":
        server.run("stdio")
        return

    import uvicorn

    app = protected_http_app(server, load_token(), host=args.host)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
