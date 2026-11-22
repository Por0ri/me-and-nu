"""Prospective content-ranking evaluation from canonical service reactions.

Capture the stored recommendation candidate order before observing a future
positive reaction. This is not the final displayed feed, a randomized online
experiment, or the historical held-out anime benchmark. PostgreSQL is read-only;
private local state keeps only HMAC identities and frozen candidate metadata.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Protocol
from urllib.parse import urlsplit

POSITIVE = frozenset({"like", "bookmark", "preference_positive"})
SOURCES = ("production", "local_demo")
MAX_USERS = 2_000
MAX_ITEMS = 200_000
MAX_EVENTS = 100_000
MAX_SNAPSHOTS = 20_000
MAX_CANDIDATES = 100
NEW_USER_DAYS = 7
STATE_VERSION = 1
MODEL_IDS = ("stored", "latest", "popular")


class PipelineError(ValueError):
    """A fixed error code safe for scheduler logs."""


@dataclass(frozen=True)
class Account:
    user_id: int
    tap_id: int
    joined_at: datetime


@dataclass(frozen=True)
class Candidate:
    content_id: int
    rank: int
    published_at: datetime


@dataclass(frozen=True)
class Round:
    round_id: int
    user_id: int
    tap_id: int
    algorithm_version: str
    created_at: datetime
    items: tuple[Candidate, ...]


@dataclass(frozen=True)
class Event:
    event_id: int
    user_id: int
    tap_id: int
    content_id: int
    event_type: str
    occurred_at: datetime
    received_at: datetime
    data_source: str
    origin: str = "server_reaction"
    schema_version: int = 1


@dataclass(frozen=True)
class DatabaseSnapshot:
    database: str
    now: datetime
    accounts: tuple[Account, ...]
    rounds: tuple[Round, ...]
    events: tuple[Event, ...]


class SnapshotSource(Protocol):
    def read(self, retention_days: int) -> DatabaseSnapshot: ...


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PipelineError("invalid_timestamp")
    return value.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return utc(value).isoformat()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def token(key: bytes, kind: str, value: int) -> str:
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def private_directory(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise PipelineError("absolute_private_path_required")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PipelineError("symlink_path_rejected")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or (os.name != "nt" and
            (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700)):
        raise PipelineError("state_directory_must_be_private")


def private_file(path: Path) -> None:
    if path.is_symlink():
        raise PipelineError("symlink_path_rejected")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and
                (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600)):
            raise PipelineError("state_file_must_be_private")


def identity_key(state_dir: Path) -> bytes:
    path = state_dir / "identity.key"
    private_file(path)
    if not path.exists():
        if (state_dir / "service-evaluation.sqlite3").exists():
            raise PipelineError("existing_state_identity_key_missing")
        key = os.urandom(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
    key = path.read_bytes()
    if len(key) != 32:
        raise PipelineError("invalid_identity_key")
    return key


class PostgresSnapshotSource:
    def __init__(self, database_url: str, expected_database: str, data_source: str, *, connect=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", expected_database) or expected_database in {"postgres", "template0", "template1"}:
            raise PipelineError("invalid_expected_database")
        if data_source not in SOURCES:
            raise PipelineError("invalid_data_source")
        database_url = re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)
        try:
            parsed = urlsplit(database_url)
            if parsed.scheme not in {"postgres", "postgresql"} or parsed.path != "/" + expected_database:
                raise PipelineError("database_identity_mismatch")
        except ValueError:
            raise PipelineError("database_identity_mismatch") from None
        self.url, self.database, self.source, self.connect = database_url, expected_database, data_source, connect

    @staticmethod
    def bounded(cur, sql, params, maximum):
        cur.execute(sql, params)
        rows = cur.fetchmany(maximum + 1)
        if len(rows) > maximum:
            raise PipelineError("collection_limit_exceeded")
        return rows

    def read(self, retention_days: int) -> DatabaseSnapshot:
        if self.connect is None:
            import psycopg
            connect = psycopg.connect
        else:
            connect = self.connect
        try:
            with connect(self.url, connect_timeout=10,
                         options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    cur.execute("SELECT current_database(), transaction_timestamp(), current_setting('transaction_read_only')")
                    database, now, readonly = cur.fetchone()
                    if database != self.database or readonly != "on":
                        raise PipelineError("readonly_database_identity_mismatch")
                    now = utc(now)
                    account_rows = self.bounded(cur, """SELECT u.user_id, t.tap_id, u.joined_at
                        FROM user_account u JOIN tap t ON t.user_id=u.user_id
                        JOIN topic p ON p.topic_id=t.topic_id
                        WHERE u.withdrawn_at IS NULL AND u.account_status IN ('active','warned')
                          AND u.onboarding_completed_at IS NOT NULL AND t.deleted_at IS NULL
                          AND p.topic_code='anime' ORDER BY u.user_id,t.tap_id LIMIT %s""", (MAX_USERS + 1,), MAX_USERS)
                    accounts = tuple(Account(*row) for row in account_rows)
                    taps = [account.tap_id for account in accounts]
                    if not taps:
                        return DatabaseSnapshot(database, now, accounts, (), ())
                    rows = self.bounded(cur, """WITH recent AS (
                        SELECT r.*, row_number() OVER (PARTITION BY r.tap_id ORDER BY r.created_at DESC,r.recommendation_round_id DESC) AS n
                        FROM recommendation_round r WHERE r.tap_id=ANY(%s) AND r.created_at >= %s AND r.created_at <= %s)
                        SELECT r.recommendation_round_id,t.user_id,r.tap_id,r.algorithm_version,r.created_at,
                               i.content_id,i.rank,COALESCE(c.published_at,c.created_at)
                        FROM recent r JOIN tap t ON t.tap_id=r.tap_id
                        JOIN recommendation_item i ON i.recommendation_round_id=r.recommendation_round_id
                        JOIN content c ON c.content_id=i.content_id
                        WHERE r.n=1 AND i.rank BETWEEN 1 AND 100 AND c.deleted_at IS NULL AND c.status='active'
                          AND COALESCE(c.published_at,c.created_at) <= %s
                        ORDER BY r.recommendation_round_id,i.rank LIMIT %s""",
                        (taps, now - timedelta(days=2), now, now, MAX_ITEMS + 1), MAX_ITEMS)
                    grouped = {}
                    for rid, uid, tap, version, created, cid, rank, published in rows:
                        group = grouped.setdefault(rid, [uid, tap, version, utc(created), []])
                        group[4].append(Candidate(cid, rank, utc(published)))
                    rounds = tuple(Round(rid, *values[:4], tuple(values[4])) for rid, values in grouped.items())
                    event_rows = self.bounded(cur, """SELECT e.user_event_id,e.user_id,e.tap_id,e.content_id,
                               e.event_type,e.occurred_at,e.received_at,e.data_source
                        FROM user_event e WHERE e.tap_id=ANY(%s) AND e.data_source=%s
                          AND e.event_value->>'origin'='server_reaction'
                          AND e.event_value->'schemaVersion'='1'::jsonb
                          AND e.event_type=ANY(%s) AND e.received_at >= %s AND e.received_at <= %s
                          AND e.occurred_at <= %s
                        ORDER BY e.received_at,e.user_event_id LIMIT %s""",
                        (taps, self.source, sorted(POSITIVE), now - timedelta(days=retention_days), now, now, MAX_EVENTS + 1), MAX_EVENTS)
                    events = tuple(Event(*row) for row in event_rows)
                conn.rollback()
            return DatabaseSnapshot(database, now, accounts, rounds, events)
        except PipelineError:
            raise
        except Exception:
            raise PipelineError("collection_unavailable") from None


def trusted_events(data: DatabaseSnapshot, source: str) -> list[Event]:
    owners = {(a.user_id, a.tap_id) for a in data.accounts}
    seen = set()
    events = []
    for event in sorted(data.events, key=lambda e: (utc(e.received_at), e.event_id)):
        if (event.event_id in seen or (event.user_id, event.tap_id) not in owners
                or event.data_source != source or event.origin != "server_reaction"
                or type(event.schema_version) is not int or event.schema_version != 1
                or event.event_type not in POSITIVE):
            continue
        seen.add(event.event_id)
        # Canonical mutation events are server-timed. Client clocks and replayed
        # browser measurement events must never become evaluation labels.
        if utc(event.occurred_at) != utc(event.received_at) or utc(event.received_at) > utc(data.now):
            continue
        events.append(event)
    return events


def initialize(db: sqlite3.Connection, config: dict) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        round_key TEXT PRIMARY KEY,user_key TEXT NOT NULL,tap_key TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,captured_at TEXT NOT NULL,cohort TEXT NOT NULL,
        ranks_json TEXT NOT NULL,feature_hash TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS snapshot_user_captured ON snapshots(user_key,captured_at)")
    previous = db.execute("SELECT value FROM metadata WHERE key='configuration'").fetchone()
    expected = canonical(config)
    if previous and previous[0] != expected:
        raise PipelineError("state_configuration_mismatch")
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('configuration',?)", (expected,))


def metadata(db, name):
    row = db.execute("SELECT value FROM metadata WHERE key=?", (name,)).fetchone()
    return row[0] if row else None


def freeze(db, data, key, source, retention_days, events) -> int:
    now = utc(data.now)
    accounts = {(a.user_id, a.tap_id): a for a in data.accounts}
    active = {(token(key, "user", a.user_id), token(key, "tap", a.tap_id)) for a in data.accounts}
    # Withdrawn/inactive people and expired snapshots leave private state too.
    for user_key, tap_key in db.execute("SELECT DISTINCT user_key,tap_key FROM snapshots").fetchall():
        if (user_key, tap_key) not in active:
            db.execute("DELETE FROM snapshots WHERE user_key=? AND tap_key=?", (user_key, tap_key))
    db.execute("DELETE FROM snapshots WHERE captured_at < ?", (stamp(now - timedelta(days=retention_days)),))
    popularity = Counter({})
    counted = set()
    for event in events:
        pair = event.user_id, event.content_id
        if pair not in counted and utc(event.received_at) < now:
            counted.add(pair)
            popularity[event.content_id] += 1
    collected = 0
    newest_version = None
    for round_ in sorted(data.rounds, key=lambda r: (utc(r.created_at), r.round_id)):
        account = accounts.get((round_.user_id, round_.tap_id))
        if account is None or not now - timedelta(days=2) <= utc(round_.created_at) <= now:
            continue
        if utc(account.joined_at) > now:
            continue
        round_key = token(key, "round", round_.round_id)
        candidates = sorted(round_.items, key=lambda c: (c.rank, c.content_id))
        if (not candidates or len(candidates) > MAX_CANDIDATES or
                len({c.content_id for c in candidates}) != len(candidates) or
                len({c.rank for c in candidates}) != len(candidates) or
                any(type(c.content_id) is not int or c.content_id <= 0 or type(c.rank) is not int
                    or not 1 <= c.rank <= MAX_CANDIDATES or utc(c.published_at) > now for c in candidates)):
            continue
        if not isinstance(round_.algorithm_version, str) or not 1 <= len(round_.algorithm_version) <= 40:
            continue
        newest_version = round_.algorithm_version
        if db.execute("SELECT 1 FROM snapshots WHERE round_key=?", (round_key,)).fetchone():
            continue
        stored = [c.content_id for c in candidates]
        latest = [c.content_id for c in sorted(candidates, key=lambda c: (utc(c.published_at), c.content_id), reverse=True)]
        popular = [c.content_id for c in sorted(candidates, key=lambda c: (-popularity[c.content_id], -utc(c.published_at).timestamp(), -c.content_id))]
        ranks = {"stored": stored, "latest": latest, "popular": popular}
        feature_hash = sha({"algorithm": round_.algorithm_version, "capturedAt": stamp(now),
                            "candidates": [[c.content_id, c.rank, stamp(c.published_at), popularity[c.content_id]] for c in candidates]})
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)", (
            round_key, token(key, "user", account.user_id), token(key, "tap", account.tap_id),
            round_.algorithm_version, stamp(now),
            "new" if now - utc(account.joined_at) < timedelta(days=NEW_USER_DAYS) else "existing",
            canonical(ranks), feature_hash,
        ))
        collected += 1
    if newest_version is not None:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('current_model_version',?)", (newest_version,))
    return collected


def cases_for_evaluation(db, data, events, key, observation_days):
    snapshots = db.execute("SELECT * FROM snapshots ORDER BY captured_at DESC,round_key DESC").fetchall()
    # Only one actual serving model version belongs in a comparison. The newest
    # captured version is authoritative even while it awaits mature samples.
    if not snapshots:
        return None, [], 0, 0
    version = metadata(db, "current_model_version") or snapshots[0][3]
    now = utc(data.now)
    by_tap = {}
    for event in events:
        by_tap.setdefault(token(key, "tap", event.tap_id), []).append(event)
    mature = 0
    chosen_users = set()
    cases = []
    no_response = 0
    for round_key, user_key, tap_key, algorithm, captured, cohort, ranks_json, feature_hash in snapshots:
        start = datetime.fromisoformat(captured)
        end = start + timedelta(days=observation_days)
        if algorithm != version or end > now:
            continue
        mature += 1
        if user_key in chosen_users:
            continue
        chosen_users.add(user_key)
        matching = [e for e in by_tap.get(tap_key, ()) if start < utc(e.received_at) <= end]
        if not matching:
            no_response += 1
            continue
        target = min(matching, key=lambda e: (utc(e.received_at), e.event_id)).content_id
        frozen = json.loads(ranks_json)
        ranks = {model: (frozen[model].index(target) + 1 if target in frozen[model][:10] else None) for model in MODEL_IDS}
        cases.append({"caseKey": round_key, "cohort": cohort, "capturedAt": captured,
                      "endsAt": stamp(end), "featureHash": feature_hash, "ranks": ranks})
    return version, cases, mature, no_response


def build_artifact(cases, version, now, source, observation_days, min_samples, code_hash, retention_days=90):
    groups = {name: sorted([case for case in cases if case["cohort"] == name], key=lambda c: c["caseKey"])
              for name in ("existing", "new")}
    included = {name: rows for name, rows in groups.items() if len(rows) >= min_samples}
    if not included:
        return None, None
    rows = [case for cohort in included.values() for case in cohort]
    input_hash = sha({"cases": rows, "algorithm": version, "source": source, "observationDays": observation_days,
                      "minSamples": min_samples, "retentionDays": retention_days,
                      "codeSha256": code_hash, "policy": STATE_VERSION})
    version_id = version if re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", version) else "stored-" + sha(version)[:24]
    result = {
        "schemaVersion": 2, "evaluationKind": "service", "dataSource": source,
        "evaluationId": "service-" + input_hash[:40],
        "evaluatedAt": stamp(now), "k": 10, "groundTruthPerCase": 1, "split": "prospective",
        "currentModelId": "stored", "baselineModelId": "latest",
        "models": [{"modelId": "stored", "label": f"저장 추천 ({version})", "version": version_id},
                   {"modelId": "latest", "label": "최신순 기준 모델", "version": "latest-" + code_hash[:24]},
                   {"modelId": "popular", "label": "긍정 반응 인기순 기준 모델", "version": "popular-" + code_hash[:24]}],
        "conditions": {
            "evaluationData": f"서비스 미래 긍정 행동 평가; data_source={source}; 집단별 최소 {min_samples}명",
            "datasetSha256": input_hash,
            "inputConditions": f"예측 수집 시점의 저장 추천 후보·발행 시각·최근 {retention_days}일 서버 긍정 사용자 수를 고정. 코드 SHA256={code_hash}",
            "aggregationUnit": "content",
            "commonRules": "세 모델 모두 같은 저장 추천 후보 최대 100개; 상위 10개 비교. 후보 밖 정답은 miss. 사용자당 최신 성숙 snapshot 1개; 무반응은 미관측으로 제외.",
            "targetPeriod": min(c["capturedAt"] for c in rows) + " through " + max(c["endsAt"] for c in rows),
            "groundTruth": f"고정 후 {observation_days}일 내 첫 서버 like/bookmark/preference_positive 콘텐츠 1개. 이후 취소와 무관한 행동 발생 정답이며 시청 완료를 뜻하지 않음.",
            "candidatePolicy": "저장 추천 후보 내 순위 평가. 실제 피드의 후속 재배열·노출 위치·온라인 인과 효과를 평가하지 않음. 최신순/인기순은 수집 당시 동일 후보만 재정렬.",
            "recordSource": f"canonical server_reaction v1; source={source}; serving algorithm={version}; version identifier SHA256={sha(version)}; frozen predictions SHA256={sha([c['featureHash'] for c in rows])}. 모델 가중치 파일 해시는 원본에 기록되지 않음.",
            "cohortInterpretation": "disjoint_users",
        },
        "cohorts": [{"audience": name, "definition": f"예측 수집 시점 가입 {NEW_USER_DAYS}일 " + ("미만" if name == "new" else "이상") + "; 사용자당 하나의 최신 성숙 예측",
                     "caseSetSha256": sha([c["caseKey"] for c in cohort]), "sampleSize": len(cohort),
                     "models": [{"modelId": model, "targetRanks": [c["ranks"][model] for c in cohort]} for model in MODEL_IDS]}
                    for name, cohort in included.items()],
    }
    return result, input_hash


def write_artifact(artifact, output: Path):
    from model.eval.admin_performance_export import publish_artifact
    publish_artifact(artifact, output)
    if os.name != "nt":
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def run_pipeline(*, state_dir: Path, output: Path, source: SnapshotSource, expected_database: str,
                 data_source: str, observation_days=7, min_samples=20, retention_days=90,
                 writer=write_artifact, code_hash=None) -> dict:
    if data_source not in SOURCES or not 1 <= observation_days <= 30 or not 1 <= min_samples <= 100_000:
        raise PipelineError("invalid_policy")
    if not observation_days + 2 <= retention_days <= 180:
        raise PipelineError("invalid_retention")
    if output == state_dir or state_dir in output.parents:
        raise PipelineError("output_must_be_separate_from_private_state")
    private_directory(state_dir)
    private_directory(output.parent)
    private_file(output)
    key = identity_key(state_dir)
    state_path = state_dir / "service-evaluation.sqlite3"
    private_file(state_path)
    if not state_path.exists():
        fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    code_hash = code_hash or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    config = {"schemaVersion": STATE_VERSION, "database": expected_database, "dataSource": data_source,
              "observationDays": observation_days, "newUserDays": NEW_USER_DAYS}
    db = sqlite3.connect(state_path, timeout=5)
    try:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN IMMEDIATE")
        initialize(db, config)
        data = source.read(retention_days)
        if data.database != expected_database:
            raise PipelineError("database_identity_mismatch")
        utc(data.now)
        if len(data.accounts) > MAX_USERS or len(data.events) > MAX_EVENTS:
            raise PipelineError("collection_limit_exceeded")
        last_run = metadata(db, "last_run_at")
        if last_run and utc(data.now) < datetime.fromisoformat(last_run):
            raise PipelineError("clock_moved_backwards")
        events = trusted_events(data, data_source)
        freeze(db, data, key, data_source, retention_days, events)
        # Evaluation uses only each user's latest mature prediction. Keep that
        # prediction and still-observing windows, not ninety redundant daily
        # copies. Never fall back to an older prediction after no response.
        db.execute("""DELETE FROM snapshots WHERE round_key IN (
            SELECT round_key FROM (
                SELECT round_key,row_number() OVER (
                    PARTITION BY user_key,algorithm_version ORDER BY captured_at DESC,round_key DESC) AS n
                FROM snapshots WHERE captured_at <= ?
            ) WHERE n > 1)""", (stamp(utc(data.now) - timedelta(days=observation_days)),))
        collected = db.execute("SELECT count(*) FROM snapshots").fetchone()[0]
        if collected > MAX_SNAPSHOTS:
            raise PipelineError("private_state_limit_exceeded")
        version, cases, mature, no_response = cases_for_evaluation(db, data, events, key, observation_days)
        sizes = {name: sum(case["cohort"] == name for case in cases) for name in ("existing", "new")}
        result = {"state": "waiting", "reason": "insufficient_samples", "artifactWritten": False,
                  "dataSource": data_source, "observationDays": observation_days, "minSamples": min_samples,
                  "collectedCases": collected, "matureCases": mature, "eligibleCases": len(cases),
                  "unobservedCases": no_response, "cohortSampleSizes": sizes,
                  "lastEvaluationId": metadata(db, "last_evaluation_id")}
        if not mature:
            result["reason"] = "awaiting_observation_window" if db.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone() else "no_recommendation_snapshots"
        artifact, fingerprint = build_artifact(cases, version, data.now, data_source, observation_days, min_samples, code_hash, retention_days)
        if artifact is not None:
            if fingerprint == metadata(db, "last_input_hash"):
                result.update(state="unchanged", reason="same_evaluation_input",
                              evaluatedAt=metadata(db, "last_evaluated_at"))
            else:
                # Never commit a completed fingerprint before the validated
                # candidate file is durable. The host runner retains pending
                # candidates until publication succeeds.
                writer(artifact, output)
                for name, value in (("last_input_hash", fingerprint), ("last_evaluation_id", artifact["evaluationId"]),
                                    ("last_evaluated_at", artifact["evaluatedAt"])):
                    db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, value))
                result.update(state="published", reason="candidate_ready", artifactWritten=True,
                              lastEvaluationId=artifact["evaluationId"], evaluatedAt=artifact["evaluatedAt"])
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_run_at',?)", (stamp(data.now),))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--data-source", required=True, choices=SOURCES)
    parser.add_argument("--observation-days", type=int, default=7)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--retention-days", type=int, default=90)
    args = parser.parse_args(argv)
    try:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise PipelineError("database_environment_required")
        source = PostgresSnapshotSource(url, args.expected_database, args.data_source)
        result = run_pipeline(state_dir=args.state_dir, output=args.output, source=source,
                              expected_database=args.expected_database, data_source=args.data_source,
                              observation_days=args.observation_days, min_samples=args.min_samples,
                              retention_days=args.retention_days)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, PipelineError) else "evaluation_pipeline_failed"
        print(json.dumps({"state": "error", "reason": reason, "artifactWritten": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
