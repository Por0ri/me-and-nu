"""Role-specific probes. Errors never echo connection URLs or credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys
from urllib.request import urlopen

from menu_worker.app import app, settings
from menu_worker.tasks import HEARTBEAT_KEY, result_store


def check(role: str) -> bool:
    nodes = {"worker": "runtime", "job-worker": "content", "anime-worker": "anime", "generation-worker": "generate"}
    if role in nodes:
        worker_app = app
        if role == "anime-worker":
            from menu_worker.anime_app import app as worker_app
        elif role == "generation-worker":
            from menu_worker.generation_app import app as worker_app
        name = f"{nodes[role]}@{socket.gethostname()}"
        return any(reply.get(name, {}).get("ok") == "pong"
                   for reply in worker_app.control.ping(destination=[name], timeout=3))
    if role in {"job-dispatcher", "anime-dispatcher", "generation-dispatcher"}:
        stamp = datetime.fromisoformat(Path("/tmp/job-dispatcher.health").read_text())
        return 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 30
    if role == "beat":
        # This is end-to-end freshness: Beat dispatch AND Worker consumption.
        with result_store() as client:
            value = client.get(HEARTBEAT_KEY)
        if not value:
            return False
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(json.loads(value)["timestamp"])).total_seconds()
        return 0 <= age <= settings.beat_interval_seconds * 2 + 15
    if role == "flower":
        with urlopen("http://127.0.0.1:5555/healthcheck", timeout=3) as response:
            return response.status == 200
    raise ValueError("Unknown runtime role")


def main() -> int:
    try:
        return 0 if len(sys.argv) == 2 and check(sys.argv[1]) else 1
    except Exception:
        print("Runtime health check failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
