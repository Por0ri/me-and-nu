"""Host exporter output is consumed verbatim by the API's strict file reader."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest

from app.services.admin_generation_service import get_generation_status


def exporter_fixture():
    path = Path(__file__).resolve().parents[2] / "deploy/generation-observability/test_export_snapshot.py"
    spec = importlib.util.spec_from_file_location("generation_export_contract_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scenario,availability", [
    ("complete", "available"), ("inflight", "partial"),
    ("historical_unknown", "partial"), ("unavailable", "unavailable"), ("rollover", "partial"),
    ("uncapped", "available"),
])
def test_atomic_exporter_to_api_has_no_schema_translation(tmp_path, scenario, availability):
    fixture = exporter_fixture()
    value = fixture.state([fixture.record(rag=True)])
    now = fixture.NOW
    if scenario == "inflight":
        value = fixture.state([fixture.record(rag=True, status="running", outcome=None, complete=False, cost=None)])
    elif scenario == "uncapped":
        records = [fixture.record(rag=True, outcome="ready_for_staging" if index < 20 else "held")
                   for index in range(61)]
        # Distinct durable reservations are required, including distinct RAG identities.
        from uuid import NAMESPACE_URL, uuid5
        for index, record in enumerate(records):
            record["job_id"] = str(uuid5(NAMESPACE_URL, f"uncapped-export-{index}"))
            record["canonical_url"] = "urn:menu-rag:" + fixture.m.digest({"index": index})
        value = fixture.state(records, max_attempts=None)
    elif scenario == "rollover":
        now = datetime(2026, 11, 27, 15, tzinfo=timezone.utc)
    source = fixture.FakeSources(value, unchanged=scenario != "unavailable")
    if scenario == "historical_unknown":
        old, receipts = fixture.recovered()
        source = fixture.FakeSources(fixture.state([fixture.record(rag=True)], [old], receipts))
        source.receipt = receipts.__getitem__
    snapshot = fixture.m.collect(factory=lambda: source, now=now)
    fixture.m.atomic_write(tmp_path, snapshot)
    result = get_generation_status(snapshot_path=str(tmp_path / "generation.json"), now=now)
    assert result.availability == availability
    assert result.reason != "snapshot_invalid"
    if scenario == "complete":
        assert result.daily.attempts == result.daily.rag_attempts == result.daily.passes == 1
        assert result.daily.total_estimated_cost_usd == "0.05"
        assert result.history.total_estimated_cost_usd == "0.06"
    elif scenario == "inflight":
        assert result.daily.inflight_cost_jobs == 1 and result.daily.total_estimated_cost_usd is None
    elif scenario == "historical_unknown":
        assert result.daily.unknown_cost_jobs == 0 and result.history.unknown_cost_jobs == 1
        assert result.history.known_estimated_cost_usd == "0.08"
        assert result.history.total_estimated_cost_usd is None
    elif scenario == "rollover":
        assert result.daily is None and result.jobs is None and result.history is not None
    elif scenario == "uncapped":
        assert result.daily.attempts == result.daily.rag_attempts == 61
        assert result.daily.max_attempts is result.daily.remaining_attempts is None
        assert result.daily.passes == result.daily.success_target == 20
        assert result.daily.remaining_passes == 0
        assert result.daily.total_estimated_cost_usd == "3.05"
    else:
        assert result.daily is None and result.history is None and result.broker is None


def test_same_day_limit_revision_preserves_old_attempts_passes_and_legacy_policy(tmp_path):
    fixture = exporter_fixture()
    from uuid import NAMESPACE_URL, uuid5

    def records(prefix, count, passed):
        rows = []
        for index in range(count):
            row = fixture.record(rag=True, outcome="ready_for_staging" if index < passed else "held")
            row["job_id"] = str(uuid5(NAMESPACE_URL, f"{prefix}-{index}"))
            row["canonical_url"] = "urn:menu-rag:" + fixture.m.digest({"prefix": prefix, "index": index})
            rows.append(row)
        return rows

    previous = records("existing", 9, 6)
    value = fixture.revised_state(records("continued", 36, 14), prior_rows=previous)
    assert value["daily_policy"]["schema_version"] == 3
    assert value["daily_policy"]["max_attempts"] == 40
    before = fixture.m.digest(value["records"][:9])
    observed = fixture.m.collect(factory=lambda: fixture.FakeSources(value), now=fixture.NOW)
    fixture.m.atomic_write(tmp_path, observed)
    response = get_generation_status(snapshot_path=str(tmp_path / "generation.json"), now=fixture.NOW)
    assert response.reason != "snapshot_invalid"
    assert response.daily.attempts == 45 and response.daily.passes == 20
    assert response.daily.max_attempts is response.daily.remaining_attempts is None
    assert response.daily.remaining_passes == 0
    assert response.daily.known_estimated_cost_usd == "2.25"
    assert fixture.m.digest(value["records"][:9]) == before
