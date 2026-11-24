"""Prospective collection, time isolation, anonymous state and safe publication."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from model.eval import service_pipeline as pipeline

NOW = datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


class Source:
    def __init__(self, data):
        self.data = data

    def read(self, retention_days):
        assert retention_days == 90
        return self.data


def data(now=NOW, *, rounds=True, events=(), accounts=None, version="v1.1-beta"):
    accounts = accounts or (pipeline.Account(909090901, 818181801, NOW - 20 * DAY),
                            pipeline.Account(909090902, 818181802, NOW - DAY))
    items = (pipeline.Candidate(101, 1, NOW - 3 * DAY), pipeline.Candidate(102, 2, NOW - 2 * DAY),
             pipeline.Candidate(103, 3, NOW - DAY))
    result = tuple(pipeline.Round(707070700 + i, a.user_id, a.tap_id, version, NOW - timedelta(hours=1), items)
                   for i, a in enumerate(accounts)) if rounds else ()
    return pipeline.DatabaseSnapshot("evaluation_test", now, accounts, result, tuple(events))


def event(number, user_index=0, target=102, at=NOW + DAY, **changes):
    result = pipeline.Event(number, 909090901 + user_index, 818181801 + user_index,
                            target, "like", at, at, "production")
    return replace(result, **changes)


@pytest.fixture
def run(tmp_path):
    def execute(snapshot, **kwargs):
        return pipeline.run_pipeline(state_dir=tmp_path / "state", output=tmp_path / "output" / "candidate.json",
            source=Source(snapshot), expected_database="evaluation_test", data_source="production",
            observation_days=7, min_samples=kwargs.pop("min_samples", 1), code_hash="c" * 64, **kwargs)
    return execute


def read_artifact(tmp_path):
    return json.loads((tmp_path / "output/candidate.json").read_text(encoding="utf-8"))


def test_freezes_before_future_labels_then_publishes_true_content_contract(run, tmp_path):
    initial = run(data())
    assert initial["state"] == "waiting" and initial["reason"] == "awaiting_observation_window"
    assert initial["collectedCases"] == 2 and not initial["artifactWritten"]
    assert not (tmp_path / "output/candidate.json").exists()
    immature = run(data(NOW + 6 * DAY, rounds=False, events=[event(1), event(2, 1)]))
    assert immature["state"] == "waiting" and immature["matureCases"] == 0
    assert immature["collectedCases"] == 2
    ready = run(data(NOW + 7 * DAY, rounds=False, events=[event(1), event(2, 1)]))
    assert ready["state"] == "published" and ready["cohortSampleSizes"] == {"existing": 1, "new": 1}
    artifact = read_artifact(tmp_path)
    assert (artifact["schemaVersion"], artifact["evaluationKind"], artifact["split"]) == (2, "service", "prospective")
    assert artifact["conditions"]["aggregationUnit"] == "content"
    assert artifact["dataSource"] == "production"
    assert artifact["conditions"]["cohortInterpretation"] == "disjoint_users"
    assert artifact["models"][0]["label"] == "저장 추천 (v1.1-beta)"
    assert all(model["targetRanks"] == [2] for cohort in artifact["cohorts"] for model in cohort["models"])
    assert "실제 피드" in artifact["conditions"]["candidatePolicy"]


def test_first_positive_target_outside_candidates_is_observed_miss(run, tmp_path):
    run(data())
    result = run(data(NOW + 8 * DAY, rounds=False, events=[event(1, target=999), event(2, target=101, at=NOW+2*DAY)]))
    assert result["state"] == "published" and result["unobservedCases"] == 1
    artifact = read_artifact(tmp_path)
    assert [c["audience"] for c in artifact["cohorts"]] == ["existing"]
    assert all(m["targetRanks"] == [None] for m in artifact["cohorts"][0]["models"])


def test_unobserved_users_are_not_zero_scores_and_minimum_is_per_cohort(run, tmp_path):
    run(data())
    result = run(data(NOW + 8 * DAY, rounds=False, events=[event(1)]), min_samples=20)
    assert result["state"] == "waiting" and result["eligibleCases"] == 1
    assert result["unobservedCases"] == 1
    assert not (tmp_path / "output/candidate.json").exists()


@pytest.mark.parametrize("changes", [
    {"origin": "client"}, {"data_source": "local_demo"}, {"data_source": "automated_test"},
    {"schema_version": "1"}, {"schema_version": True}, {"event_type": "open"},
    {"occurred_at": NOW - DAY}, {"tap_id": 42}, {"user_id": 42},
])
def test_only_canonical_same_source_server_timed_owned_labels_count(run, changes):
    run(data())
    result = run(data(NOW + 8 * DAY, rounds=False, events=[event(1, **changes)]))
    assert result["state"] == "waiting" and result["eligibleCases"] == 0


def test_future_events_do_not_leak_into_frozen_popularity(run, tmp_path):
    # Only prior canonical positive users affect popularity, despite input
    # containing future rows (a defensive guard beyond the SQL snapshot).
    run(data(events=[event(1, target=101, at=NOW - DAY), event(2, 1, 103, NOW + DAY)]))
    result = run(data(NOW + 8 * DAY, rounds=False, events=[event(3, target=101)]))
    assert result["state"] == "published"
    ranks = {m["modelId"]: m["targetRanks"] for m in read_artifact(tmp_path)["cohorts"][0]["models"]}
    assert ranks == {"stored": [1], "latest": [3], "popular": [1]}


def test_prediction_and_observation_cutoffs_reject_before_and_after_window(run):
    run(data())
    events = [event(1, at=NOW), event(2, at=NOW + 7 * DAY + timedelta(microseconds=1))]
    result = run(data(NOW + 8 * DAY, rounds=False, events=events))
    assert result["eligibleCases"] == 0


def test_unchanged_input_reuses_completion_time_without_writing(run, tmp_path):
    run(data())
    first = run(data(NOW + 8 * DAY, rounds=False, events=[event(1)]))
    before = (tmp_path / "output/candidate.json").read_bytes()
    second = run(data(NOW + 9 * DAY, rounds=False, events=[event(1), event(1)]))
    assert second["state"] == "unchanged" and not second["artifactWritten"]
    assert second["evaluatedAt"] == first["evaluatedAt"]
    assert second["lastEvaluationId"] == first["lastEvaluationId"]
    assert (tmp_path / "output/candidate.json").read_bytes() == before


def test_snapshot_and_artifact_have_no_raw_user_tap_or_round_identity(run, tmp_path):
    run(data())
    report = run(data(NOW + 8 * DAY, rounds=False, events=[event(1)]))
    for path in (tmp_path / "state/service-evaluation.sqlite3", tmp_path / "output/candidate.json"):
        stored = path.read_bytes()
        for raw in (b"909090901", b"818181801", b"707070700"):
            assert raw not in stored
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "caseKey" not in (tmp_path / "output/candidate.json").read_text(encoding="utf-8")
    assert "909090901" not in json.dumps(report)


def test_failed_output_does_not_commit_fingerprint(run, tmp_path):
    run(data())
    mature = data(NOW + 8 * DAY, rounds=False, events=[event(1)])
    def fail(*_):
        raise OSError("synthetic disk full")
    with pytest.raises(OSError):
        run(mature, writer=fail)
    with sqlite3.connect(tmp_path / "state/service-evaluation.sqlite3") as db:
        assert pipeline.metadata(db, "last_input_hash") is None
    assert run(mature)["state"] == "published"


def test_new_model_version_waits_without_mixing_previous_version(run, tmp_path):
    run(data())
    mature = data(NOW + 8 * DAY, rounds=False, events=[event(1)])
    assert run(mature)["state"] == "published"
    candidate = tmp_path / "output/candidate.json"
    previous = candidate.read_bytes()
    new_round = replace(data().rounds[0], round_id=9999, algorithm_version="v2.0", created_at=NOW + 8 * DAY)
    newest = replace(mature, now=NOW + 8 * DAY + timedelta(hours=1), rounds=(new_round,))
    status = run(newest)
    assert status["state"] == "waiting" and status["matureCases"] == 0
    assert candidate.read_bytes() == previous


def test_latest_mature_snapshot_per_user_prevents_double_cohort_membership(run, tmp_path):
    run(data())
    # The same initially new account now has an existing-cohort prediction.
    account = data().accounts[1]
    later_round = replace(data().rounds[1], round_id=999, created_at=NOW + 8 * DAY)
    later = pipeline.DatabaseSnapshot("evaluation_test", NOW + 8 * DAY, (account,), (later_round,), (event(1, 1),))
    run(later)
    result = run(replace(later, now=NOW + 16 * DAY, rounds=(), events=(event(1, 1), event(2, 1, at=NOW + 9 * DAY))))
    assert result["cohortSampleSizes"] == {"existing": 1, "new": 0}
    assert [c["audience"] for c in read_artifact(tmp_path)["cohorts"]] == ["existing"]
    # Older mature predictions have no role in this evaluation; discard them
    # so daily collection stays bounded while the latest window matures.
    assert result["collectedCases"] == 1
    with sqlite3.connect(tmp_path / "state/service-evaluation.sqlite3") as db:
        assert db.execute("SELECT captured_at FROM snapshots").fetchone()[0] == pipeline.stamp(NOW + 8 * DAY)


def test_newest_mature_unobserved_case_does_not_fall_back_to_older_positive(run):
    run(data())
    account = data().accounts[0]
    later_round = replace(data().rounds[0], round_id=999, created_at=NOW + 8 * DAY)
    later = pipeline.DatabaseSnapshot("evaluation_test", NOW + 8 * DAY, (account,), (later_round,), (event(1),))
    assert run(later)["eligibleCases"] == 1
    result = run(replace(later, now=NOW + 16 * DAY, rounds=()))
    assert result["collectedCases"] == 1 and result["eligibleCases"] == 0
    assert result["unobservedCases"] == 1 and result["state"] == "waiting"


def test_withdrawn_user_and_expired_private_snapshots_are_pruned(run, tmp_path):
    run(data())
    run(replace(data(NOW + 91 * DAY, rounds=False), accounts=()))
    with sqlite3.connect(tmp_path / "state/service-evaluation.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_deleted_tap_snapshots_are_pruned_even_when_user_remains_active(run, tmp_path):
    run(data())
    replacement = replace(data().accounts[0], tap_id=999999)
    run(replace(data(NOW + DAY, rounds=False), accounts=(replacement,)))
    with sqlite3.connect(tmp_path / "state/service-evaluation.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_source_change_clock_rollback_and_database_mismatch_fail_closed(run):
    run(data())
    with pytest.raises(pipeline.PipelineError, match="clock_moved_backwards"):
        run(data(NOW - DAY))
    with pytest.raises(pipeline.PipelineError, match="database_identity_mismatch"):
        run(replace(data(), database="wrong"))


def test_missing_identity_key_cannot_silently_replace_existing_identity_namespace(run, tmp_path):
    run(data())
    (tmp_path / "state/identity.key").unlink()
    with pytest.raises(pipeline.PipelineError, match="existing_state_identity_key_missing"):
        run(data())


def test_output_cannot_replace_private_state(tmp_path):
    state = tmp_path / "state"
    with pytest.raises(pipeline.PipelineError, match="output_must_be_separate"):
        pipeline.run_pipeline(state_dir=state, output=state / "identity.key", source=Source(data()),
            expected_database="evaluation_test", data_source="production")
    assert not state.exists()


def test_database_collector_uses_repeatable_read_readonly_and_strict_source_predicate():
    class Cursor:
        def __init__(self):
            self.commands = []
            self.index = 0
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, params=None): self.commands.append((query, params))
        def fetchone(self): return ("evaluation_test", NOW, "on")
        def fetchmany(self, _):
            self.index += 1
            return [[(1, 2, NOW - DAY)], [], []][self.index - 1]
    class Connection:
        def __init__(self): self.cur, self.rolled_back = Cursor(), False
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def cursor(self): return self.cur
        def rollback(self): self.rolled_back = True
    connection = Connection()
    options = {}
    def connect(url, **kwargs):
        assert url == "postgresql://synthetic:synthetic@127.0.0.1:1/evaluation_test"
        options.update(kwargs)
        return connection
    source = pipeline.PostgresSnapshotSource("postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/evaluation_test", "evaluation_test", "production", connect=connect)
    result = source.read(90)
    assert result.database == "evaluation_test" and connection.rolled_back
    assert "default_transaction_read_only=on" in options["options"]
    queries = [q for q, _ in connection.cur.commands]
    assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert all(not q.lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
    event_query = queries[-1]
    assert "e.data_source=%s" in event_query and "'server_reaction'" in event_query
    assert "'1'::jsonb" in event_query and "e.received_at <= %s" in event_query


def test_cli_missing_db_is_sanitized_and_does_not_read_dotenv(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = pipeline.main(["--state-dir", str(tmp_path / "state"), "--output", str(tmp_path / "output/result.json"),
                            "--expected-database", "evaluation_test", "--data-source", "production"])
    assert result == 2
    message = json.loads(capsys.readouterr().err)
    assert message == {"state": "error", "reason": "database_environment_required", "artifactWritten": False}


def test_actual_schema_accepts_service_artifact_and_provider_scores_it(run, tmp_path):
    run(data())
    run(data(NOW + 8 * DAY, rounds=False, events=[event(1, target=101)]))
    from app.schemas.admin_recommendation_analytics import RecommendationEvaluationArtifact
    artifact = RecommendationEvaluationArtifact.model_validate(read_artifact(tmp_path))
    assert artifact.evaluation_kind == "service" and artifact.conditions.aggregation_unit == "content"
    from app.services.admin_recommendation_analytics_service import get_recommendation_performance
    response = get_recommendation_performance(artifact_path=str(tmp_path / "output/candidate.json"),
                                             dataset="service", now=NOW + 9 * DAY)
    assert response.availability == "available" and response.source == "service_feedback"
    scores = {model.model_id: model.scores[0] for model in response.models}
    assert scores["stored"].ndcg_at10 == 1 and scores["stored"].hit_rate_at10 == 1
    assert scores["latest"].ndcg_at10 == 0.5 and scores["latest"].hit_rate_at10 == 1


def test_postgres_canonical_reaction_to_prospective_artifact(tmp_path, monkeypatch):
    """Opt-in real SQL test, restricted to a disposable loopback DB and own schema.

    The observation clock advances in the test adapter only: collection and
    reaction transactions use the actual PostgreSQL timestamps throughout.
    """
    value = os.environ.get("RECOMMENDATION_TEST_DATABASE_URL")
    if not value:
        pytest.skip("requires disposable local recommendation_test PostgreSQL")
    import asyncio
    from uuid import uuid4
    import psycopg
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    from app.core.config import settings
    from app.db.base import Base
    from app.models import Content, RecommendationItem, RecommendationRound, Tap, Topic, UserAccount
    from app.services import reaction_service
    from app.services.admin_recommendation_analytics_service import get_recommendation_performance

    url = make_url(value)
    if (url.host not in {"127.0.0.1", "localhost"} or url.database != "recommendation_test"
            or url.username != "recommendation_test" or not url.port or url.port == 5432 or url.query):
        pytest.fail("only disposable loopback recommendation_test DB accepted")
    schema = "service_evaluation_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    engine = None
    with admin.begin() as db:
        assert db.scalar(text("SELECT current_database()")) == "recommendation_test"
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
        engine = create_engine(scoped, poolclass=NullPool)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            now = db.scalar(text("SELECT clock_timestamp()"))
            topic = Topic(topic_code="anime", topic_name="Synthetic test", is_active=True)
            db.add(topic)
            db.flush()
            topic_id = topic.topic_id
            users, taps, contents = [], [], []
            for age in (20, 1):
                user = UserAccount(nickname="Synthetic", auth_provider="kakao", provider_user_id=uuid4().hex,
                                   joined_at=now - age * DAY, onboarding_completed_at=now - DAY)
                db.add(user)
                db.flush()
                tap = Tap(user_id=user.user_id, topic_id=topic_id)
                db.add(tap)
                db.flush()
                users.append(user.user_id)
                taps.append(tap.tap_id)
            for age in (3, 2, 1):
                content = Content(topic_id=topic_id, title="Synthetic test", status="active",
                                  published_at=now - age * DAY)
                db.add(content)
                db.flush()
                contents.append(content.content_id)
            for tap_id in taps:
                round_ = RecommendationRound(tap_id=tap_id, service_date=now.date(),
                                             algorithm_version="integration-v1", created_at=now)
                db.add(round_)
                db.flush()
                db.add_all([RecommendationItem(recommendation_round_id=round_.recommendation_round_id,
                                               content_id=cid, rank=i + 1) for i, cid in enumerate(contents)])
            db.commit()

        connection_options = []
        def connect(dsn, **kwargs):
            connection_options.append(kwargs["options"])
            kwargs["options"] += f" -c search_path={schema},public"
            return psycopg.connect(dsn, **kwargs)
        collector = pipeline.PostgresSnapshotSource(value, "recommendation_test", "production", connect=connect)
        first = collector.read(90)
        assert len(first.rounds) == 2 and len(first.rounds[0].items) == 3 and first.events == ()
        state, output = tmp_path / "pg-state", tmp_path / "pg-output/result.json"
        def evaluate(snapshot):
            return pipeline.run_pipeline(state_dir=state, output=output, source=Source(snapshot),
                expected_database="recommendation_test", data_source="production", min_samples=1)
        assert evaluate(first)["state"] == "waiting"

        monkeypatch.setattr(settings, "event_data_source", "production")
        async def react():
            asynchronous = create_async_engine(scoped, poolclass=NullPool)
            try:
                factory = async_sessionmaker(asynchronous, expire_on_commit=False)
                for user_id in users:
                    async with factory() as db:
                        await reaction_service.set_like(db, user_id, topic_id, contents[0])
                        await reaction_service.set_like(db, user_id, topic_id, contents[0])  # idempotent retry
            finally:
                await asynchronous.dispose()
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(react())
        second = collector.read(90)
        assert len(second.events) == 2
        assert all(e.occurred_at == e.received_at and e.received_at > first.now for e in second.events)
        assert all("default_transaction_read_only=on" in option for option in connection_options)
        with connect(collector.url, options="-c default_transaction_read_only=on") as readonly:
            readonly.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                readonly.execute("INSERT INTO topic (topic_code,topic_name) VALUES ('blocked','blocked')")
            readonly.rollback()

        observed = replace(second, now=second.now + 8 * DAY, rounds=())
        result = evaluate(observed)
        assert result["state"] == "published" and result["cohortSampleSizes"] == {"existing": 1, "new": 1}
        response = get_recommendation_performance(artifact_path=str(output), dataset="service", now=second.now + 9 * DAY)
        assert response.availability == "available"
        scores = {model.model_id: model.scores for model in response.models}
        assert all(score.ndcg_at10 == 1 and score.hit_rate_at10 == 1 for score in scores["stored"])
        assert all(score.ndcg_at10 == .5 for score in scores["latest"])
        assert evaluate(replace(observed, now=observed.now + DAY))["state"] == "unchanged"
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
