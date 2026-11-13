"""Live check with both dispatchers paused. Leave the queued job for its owner."""
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from menu_worker.job_checks import smoke_input
from menu_worker.job_store import JobStore
from menu_worker.jobs import send_delivery


def main():
    kind = os.environ["CONTENT_JOB_KIND"]
    foreign_kind = {"fake": "anime", "anime": "fake"}[kind]
    store = JobStore()
    key = "cross-delivery-" + uuid4().hex
    request = smoke_input(foreign_kind, "success", key)
    job = store.submit(request, job_kind=foreign_kind)

    # Valid local task name, broker and queue, intentionally wrong job ID.
    receipt = send_delivery(job["job_id"], kind).get(timeout=30)
    after = store.get(job["job_id"])
    prefix = "content@" if kind == "fake" else "anime@"
    if (receipt["processed"] is not False or receipt["worker_kind"] != kind
            or not receipt["worker"].startswith(prefix) or after["execution_status"] != "queued"
            or after["attempts"] or after["generation_started_at"] is not None):
        raise AssertionError("Foreign delivery must leave the queued job unclaimed; pause both dispatchers for this check")
    print(json.dumps({"job_id": job["job_id"], "job_kind": foreign_kind,
                      "refused_by": receipt["worker"], "attempt_count": after["attempt_count"]}))


if __name__ == "__main__":
    main()
