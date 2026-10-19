"""Check API and data-store readiness without printing connection details."""

import json
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main() -> None:
    opener = build_opener(ProxyHandler({}))
    for path in ("/health", "/health/db", "/health/redis"):
        try:
            with opener.open(f"http://127.0.0.1:8000{path}", timeout=3) as response:
                if response.status != 200 or json.load(response).get("status") != "ok":
                    raise ValueError("Service is not ready")
        except (URLError, OSError, ValueError, AttributeError):
            raise SystemExit(f"Health check failed: {path}") from None


if __name__ == "__main__":
    main()
