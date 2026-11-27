"""Sequential, candidate-aware allocation across a reviewed daily route snapshot.

Only the durable job records determine allocation. Restarting the controller
cannot reset fairness, article deduplication, cost or the shared daily limits.
"""
from collections import Counter

from menu_worker.bounded_generation import TERMINAL, money
from menu_worker.bounded_resume import require

ROUTE_FIELDS = ("source_id", "content_type", "menu_work_id")
TRANSIENT_DISCOVERY_ERRORS = frozenset({
    "discovery_listing_timeout", "discovery_listing_failed", "discovery_timeout",
})


def route_key(value):
    return tuple(value.get(key) for key in ROUTE_FIELDS)


def article_identity(record):
    if record.get("producer") == "ani_rag":
        from menu_worker.rag_contract import RagRequest
        return "ani_rag", RagRequest.parse(record["request"]["rag_request"]).identity_key
    return record["request"]["source_id"], record["canonical_url"]


def allocation_key(state, route):
    """Balance works first, then types and sources; older turns win ties.

    Failed/skipped paid attempts still count. Missing/duplicate-only routes do
    not reserve quotas: only available articles compete for the next turn.
    """
    source, kind, work = route_key(route)
    scopes = (("work", work), ("type", kind), ("work_type", work, kind),
              ("source", source), ("route", source, kind, work))
    counts, last = Counter(), {}
    start = state["daily_policy"]["start_record_index"]
    for index, row in enumerate(state["records"]):
        if not row.get("counts_slot"):
            continue
        request = row["request"]
        s, t, w = route_key(request)
        keys = (("work", w), ("type", t), ("work_type", w, t),
                ("source", s), ("route", s, t, w))
        for key in keys:
            last[key] = index
            if index >= start:
                counts[key] += 1
    return (*(counts[key] for key in scopes), *(last.get(key, -1) for key in scopes), work, kind, source)


def seen_articles(state):
    identities = {article_identity(row) for row in state["records"]}
    # Preserve legacy exclusions without making a URL from one publisher block
    # an unrelated registered source. New identities use source + canonical URL.
    recorded = {row["canonical_url"] for row in state["records"]}
    identities.update((state["config"]["source_id"], url) for url in state["seen"] if url not in recorded)
    return identities


def ready_for_new_job(controller):
    controller.check()
    if controller.budget_exceeded(money(controller.config["next_job_reserve_usd"])):
        controller.stop("estimated_budget_reserve_unavailable")
    if controller.now() + controller.config["job_timeout_seconds"] > controller.deadline:
        controller.stop("insufficient_time_for_new_job")


def collect_round(controller):
    """Bound each source scan and isolate transient listing failures by route."""
    from menu_worker.discovery_agent import DiscoveryError

    routes = controller.state["daily_policy"]["routes"]
    candidates, reports = [], []
    seen = seen_articles(controller.state)
    for route in sorted(routes, key=lambda value: allocation_key(controller.state, value)):
        ready_for_new_job(controller)
        report = {**route}
        try:
            scan = controller.discover(route["source_id"], route["content_type"], 20)
        except DiscoveryError as error:
            if str(error) not in TRANSIENT_DISCOVERY_ERRORS:
                raise
            report.update(error=str(error), eligible=0)
        else:
            require(isinstance(scan, dict), "invalid_discovery_report")
            articles = scan.get("articles", [])
            require(isinstance(articles, list) and len(articles) <= 20
                    and type(scan.get("discovered", 0)) is int and 0 <= scan.get("discovered", 0) <= 20,
                    "discovery_limit_exceeded")
            report.update({key: scan.get(key) for key in ("discovered", "unresolved", "fetch_failed", "listing_partial")})
            report["eligible"] = 0
            for article in articles:
                require(isinstance(article, dict) and route_key(article) == route_key(route), "discovery_scope_mismatch")
                fields = {**article, "idempotency_key": "bounded-validation"}
                canonical, key = controller.validate(fields)
                identity = (route["source_id"], canonical)
                if identity in seen:
                    continue
                # Multiple type routes can discover the same article. Keep the
                # alternatives until allocation picks one, then fence all of them.
                fields.update(canonical_url=canonical, idempotency_key=f"{controller.config['run_id']}:{key[:48]}")
                if not any(route_key(item) == route_key(fields) and item["canonical_url"] == canonical for item in candidates):
                    candidates.append(fields)
                    report["eligible"] += 1
        reports.append(report)
        controller.state["scans"] += 1
        controller.state["route_scans"] = reports
        controller.save()
    controller.check()
    return candidates


def run_allocated(controller):
    if controller.state["status"] in {"complete", "stopped"}:
        return controller.summary()
    for record in controller.state["records"]:
        if record["state"] not in {"complete", "duplicate"}:
            controller.handle(record)  # Finish the sole in-flight job before discovering more.
    while controller.completion_reason() is None:
        candidates = collect_round(controller)
        while candidates and controller.completion_reason() is None:
            ready_for_new_job(controller)
            fields = min(candidates, key=lambda value: allocation_key(controller.state, value))
            identity = fields["source_id"], fields["canonical_url"]
            candidates = [item for item in candidates if (item["source_id"], item["canonical_url"]) != identity]
            if identity in seen_articles(controller.state):
                continue
            record = {"request": fields, "canonical_url": fields["canonical_url"], "state": "reserved",
                      "counts_slot": True, "reserved_at": controller.now(), **controller.record_metadata()}
            controller.state["records"].append(record)
            controller.state["seen"].append(record["canonical_url"])
            controller.save()
            try:
                controller.handle(record)  # Synchronous terminal result; never enqueue a second live job.
            except Exception as error:
                if getattr(error, "code", None) != "candidate_exists":
                    raise
                other = controller.store.get(str(error.existing_job_id))
                if not other or other["execution_status"] not in TERMINAL:
                    controller.stop("foreign_pending_candidate")
                record.update(state="duplicate", counts_slot=False, existing_job_id=other["job_id"])
                controller.save()
        if controller.completion_reason() is None:
            controller.sleep(min(controller.config["scan_interval_seconds"], max(0, controller.deadline - controller.now())))
    controller.state.update(status="complete", stop_reason=controller.completion_reason())
    controller.save()
    return controller.summary()
