"""Role-specific probes. Errors never echo connection URLs or credentials."""
from datetime import datetime, timezone
import json
import socket
import sys
from urllib.request import urlopen

from menu_worker.app import app, settings
from menu_worker.tasks import HEARTBEAT_KEY, result_store


def check(role: str) -> bool:
    if role == "worker":
        name = f"runtime@{socket.gethostname()}"
        return any(reply.get(name, {}).get("ok") == "pong"
                   for reply in app.control.ping(destination=[name], timeout=3))
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
