"""Run real broker smoke checks or submit/query a bounded probe task."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from menu_worker.app import app, settings
from menu_worker.tasks import HEARTBEAT_KEY, result_store


class CheckFailed(RuntimeError):
    """A fixed diagnostic message that is safe to print."""


def submit(label: str, delay: int = 0, failures: int = 0) -> str:
    result = app.send_task("runtime.probe", kwargs={
        "label": label, "delay_seconds": delay, "failures_before_success": failures,
    }, queue="runtime")
    return result.id


def result(task_id: str, timeout: int) -> dict:
    return app.AsyncResult(task_id).get(timeout=timeout)


def flower_request(path: str, authenticated: bool, method: str = "GET") -> tuple[int, object]:
    request = Request("http://127.0.0.1:5555" + path, method=method,
                      data=b"" if method == "POST" else None)
    if authenticated:
        credential = os.environ.get("FLOWER_BASIC_AUTH")
        if not credential:
            raise CheckFailed("Run the smoke check inside the flower service")
        token = base64.b64encode(credential.encode()).decode()
        request.add_header("Authorization", "Basic " + token)
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if path.startswith("/api/") else None
    except HTTPError as error:
        return error.code, None


def smoke(timeout: int) -> dict:
    started = datetime.now(timezone.utc)
    manual = result(submit("manual-smoke"), timeout)
    retry = result(submit("retry-smoke", failures=1), timeout)
    if manual["label"] != "manual-smoke" or retry["retries"] != 1:
        raise CheckFailed("Probe result did not match the request")
    # Observe a NEW scheduler heartbeat, not a stale success from a previous run.
    deadline = time.monotonic() + timeout
    evidence = None
    while time.monotonic() < deadline:
        with result_store() as client:
            raw = client.get(HEARTBEAT_KEY)
        if raw:
            candidate = json.loads(raw)
            if datetime.fromisoformat(candidate["timestamp"]) >= started:
                evidence = candidate
                break
        time.sleep(0.5)
    if evidence is None:
        raise CheckFailed("No new Beat heartbeat was consumed before the timeout")
    if flower_request("/api/workers", False)[0] != 401:
        raise CheckFailed("Flower did not reject unauthenticated API access")
    # Flower checks read_only before worker lookup; this nonexistent name can
    # never target a real worker, even if the read_only setting is missing.
    if flower_request("/api/worker/shutdown/nonexistent-runtime-readonly-probe", True, "POST")[0] != 403:
        raise CheckFailed("Flower did not reject an authenticated control request")
    # Task completion and Flower's event receiver are asynchronous.
    deadline = time.monotonic() + min(timeout, 20)
    while time.monotonic() < deadline:
        status, workers = flower_request("/api/workers?refresh=1", True)
        task_status, task = flower_request("/api/task/info/" + manual["task_id"], True)
        if status == 200 and workers and task_status == 200 and task.get("state") == "SUCCESS":
            return {"manual": manual, "retry": retry, "beat": evidence,
                    "flower_auth": "required", "flower_read_only": True, "flower_event": "SUCCESS"}
        time.sleep(0.5)
    raise CheckFailed("Flower did not report a worker and the completed task event")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    smoke_command = commands.add_parser("smoke")
    smoke_command.add_argument("--timeout", type=int, default=settings.beat_interval_seconds + 30)
    enqueue = commands.add_parser("submit")
    enqueue.add_argument("--label", default="manual")
    enqueue.add_argument("--delay-seconds", type=int, choices=range(11), default=0)
    enqueue.add_argument("--failures-before-success", type=int, choices=range(3), default=0)
    query = commands.add_parser("result")
    query.add_argument("--task-id", required=True)
    query.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    try:
        if args.command == "submit":
            report = {"task_id": submit(args.label, args.delay_seconds, args.failures_before_success)}
        elif args.command == "result":
            report = result(args.task_id, args.timeout)
        else:
            report = smoke(args.timeout)
        print(json.dumps(report))
        return 0
    except CheckFailed as error:
        print(f"Runtime check failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # No connection strings, credential values, or provider response bodies.
        print(f"Runtime check failed ({type(error).__name__}). Inspect the runtime service logs.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
