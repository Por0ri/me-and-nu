"""Run the local FastAPI server with a PostgreSQL-compatible Windows event loop."""

import asyncio
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    server = uvicorn.Server(uvicorn.Config("app.main:app", host="localhost", port=8000))
    if sys.platform == "win32":
        # psycopg's async connection cannot run on Windows' ProactorEventLoop.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
