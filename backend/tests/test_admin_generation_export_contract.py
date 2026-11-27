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
])
def test_atomic_exporter_to_api_has_no_schema_translation(tmp_path, scenario, availability):
    fixture = exporter_fixture()
    value = fixture.state([fixture.record(rag=True)])
    now = fixture.NOW
    if scenario == "inflight":
        value = fixture.state([fixture.record(rag=True, status="running", outcome=None, complete=False, cost=None)])
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
    else:
        assert result.daily is None and result.history is None and result.broker is None
