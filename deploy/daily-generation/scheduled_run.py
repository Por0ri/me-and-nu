"""Host supervisor for an explicitly approved, count-limited KST daily schedule.

The default only reads local policy/state. No mode enables a timer.
Generation and pending-only import retain their existing application boundaries.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from uuid import uuid4

KST = timezone(timedelta(hours=9))
PROJECT = "menu-daily-generation"
SAFE_STOPS = {"deadline_reached", "insufficient_time_for_new_job"}
COMPLETIONS = {"daily_success_target_reached", "daily_attempt_limit_reached"}
POLICY_FIELDS = {"schema_version", "purpose", "release_sha", "seed_checkpoint", "seed_sha256",
                 "routes", "max_attempts", "success_target", "target"}
RAG_FIELDS = {"enabled", "scope_file", "scope_sha256", "runtime_config", "runtime_sha256", "feed_valid_days"}
RAG_COMPOSE = ("rag/compose.scheduled.yaml", "rag/compose.scheduled.active.yaml")
RAG_RUNTIME = "/run/rag/runtime.json"
RAG_INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"running":{{json .State.Running}},'
    '"image":{{json .Image}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
    '"entrypoint":{{json .Config.Entrypoint}},"command":{{json .Config.Cmd}}}')
TARGET = {"host": "postgres", "port": 5432, "database": "me_nu_checking",
          "username": "me_nu_checking", "schema": "public", "environment": "production"}
BROKER_CHECK = (
    "import json; from menu_worker.jobs import job_route; "
    "from menu_worker.daily_resume import verify_empty_generation_broker; "
    "print(json.dumps(verify_empty_generation_broker(job_route('anime_generate')[0])))"
)
RESUME_CHECK = """import json, sys
from pathlib import Path
from menu_worker.bounded_generation import local_lock, database_lock
from menu_worker.bounded_resume import digest, require
from menu_worker.daily_resume import _checked_state
from menu_worker.job_store import JobStore
from menu_worker.real_generation import read_sealed_staging_result
plan = json.loads(sys.argv[1])
require(digest(plan) == sys.argv[2], 'reviewed_resume_digest_changed')
store = JobStore()
root = Path(plan['config']['artifact_root'])
with local_lock(Path(plan['source_checkpoint'])), database_lock(store, plan['config']['run_id']) as check:
    state, _, _ = _checked_state(plan, store=store, read_result=lambda job: read_sealed_staging_result(root, job))
    check()
    print(json.dumps({'verified': True, 'generation_enabled': False, 'status': state['status']}))
"""
RECOVERY_CHECK = """import json, sys, time
from datetime import datetime
from pathlib import Path
from menu_worker.bounded_generation import local_lock, database_lock
from menu_worker.bounded_resume import digest, require
from menu_worker.daily_resume import _checked_state, verify_empty_generation_broker, validate_failure_resume
from menu_worker.failure_recovery import validate_recovery_history
from menu_worker.job_store import JobStore
from menu_worker.jobs import job_route
from menu_worker.real_generation import read_sealed_staging_result
plan = json.loads(sys.argv[1])
require(digest(plan) == sys.argv[2], 'reviewed_resume_digest_changed')
reference = {'receipt_path': sys.argv[3], 'receipt_sha256': sys.argv[4]}
store = JobStore()
root = Path(plan['config']['artifact_root'])
with local_lock(Path(plan['source_checkpoint'])), database_lock(store, plan['config']['run_id']) as check:
    state, _, _ = _checked_state(plan, store=store, read_result=lambda job: read_sealed_staging_result(root, job))
    validate_recovery_history(state, store=store, artifact_root=root)
    require(state.get('failure_recovery_audit', [])[-1:] == [reference], 'reviewed_failure_recovery_changed')
    require(any(row.get('failure_recovery_receipt') == reference for row in state['records']), 'recovery_record_missing')
    if sys.argv[5] == 'resume':
        validate_failure_resume(state, sys.argv[4], store=store)
    verify_empty_generation_broker(job_route('anime_generate')[0])
    check()
    window_open = time.time() + plan['config']['job_timeout_seconds'] < datetime.fromisoformat(plan['config']['deadline']).timestamp()
    print(json.dumps({'verified': True, 'generation_enabled': False, 'receipt_sha256': sys.argv[4],
                      'status': state['status'], 'generation_window_open': window_open}))
"""

# Keep the original scripts/commands unchanged for every schema-1 schedule.
# Mixed audits compose only read-only history/candidate dependencies. They do
# not instantiate provider adapters, open a key, or publish a delivery.
MIXED_HISTORY_SETUP = """from menu_worker.jobs import job_route
from menu_worker.rag_daily_runtime import compose_runtime, verify_empty_rag_broker
runtime = compose_runtime(plan, app=job_route('anime_generate')[0],
    runtime_path=Path('/run/rag/runtime.json'), runtime_sha256=plan['rag_runtime_sha256'], enable=False)
"""


def mixed_audit(script):
    lock = "with local_lock(Path(plan['source_checkpoint'])), database_lock"
    reader = "read_result=lambda job: read_sealed_staging_result(root, job))"
    broker = "verify_empty_generation_broker(job_route('anime_generate')[0])"
    if script.count(lock) != 1 or script.count(reader) != 1 or script.count("    check()") != 1:
        raise RuntimeError("mixed_audit_source_contract_changed")
    if script is RECOVERY_CHECK and script.count(broker) != 1:
        raise RuntimeError("mixed_recovery_broker_contract_changed")
    mixed = (script.replace(lock, MIXED_HISTORY_SETUP
            + "with local_lock(Path(plan['source_checkpoint'])), local_lock(Path(plan['checkpoint'])), database_lock")
        .replace(reader,
                 "read_result=lambda job: read_sealed_staging_result(root, job), "
                 "rag_store=runtime.store, rag_read_result=runtime.read_result)")
        .replace(broker, "verify_empty_rag_broker(runtime.app)"))
    if script is RESUME_CHECK:
        mixed = mixed.replace("    check()", "    verify_empty_rag_broker(runtime.app)\n    check()")
    return mixed


MIXED_RESUME_CHECK = mixed_audit(RESUME_CHECK)
MIXED_RECOVERY_CHECK = mixed_audit(RECOVERY_CHECK)
MIXED_BROKER_CHECK = (
    "import json; from menu_worker.jobs import job_route; "
    "from menu_worker.rag_daily_runtime import verify_empty_rag_broker; "
    "print(json.dumps(verify_empty_rag_broker(job_route('anime_generate')[0])))"
)


class ScheduleError(RuntimeError):
    """Fixed error codes only; process output and credentials stay private."""


class ActivationPreflightError(ScheduleError):
    """An unstarted, verified day may retry the same plan after its guard clears."""


def require(value, code):
    if not value:
        raise ScheduleError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(read_bytes(path)).hexdigest()


def checked_path(path, *, exists=True):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "unsafe_host_path")
    require(not any(part in {"..", "."} for part in path.parts), "unsafe_host_path")
    current = path
    while current != current.parent:
        require(not current.is_symlink(), "unsafe_host_path")
        current = current.parent
    if exists:
        require(path.exists(), "required_host_path_missing")
    return path


def read_bytes(path, limit=16 * 1024 * 1024):
    path = checked_path(path)
    require(path.is_file() and path.stat().st_size <= limit, "invalid_host_file")
    return path.read_bytes()


def read_json(path):
    def pairs(items):
        value = dict(items)
        require(len(value) == len(items), "duplicate_json_key")
        return value
    try:
        return json.loads(read_bytes(path), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ScheduleError("invalid_json") from None


def relative(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./-]+", value), "unsafe_state_relative_path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and all(p not in {".", ".."} for p in path.parts),
            "unsafe_state_relative_path")
    return path


def validate_policy(policy):
    require(isinstance(policy, dict) and type(policy.get("schema_version")) is int
            and (policy["schema_version"] == 1 and set(policy) == POLICY_FIELDS
                 or policy["schema_version"] == 2 and set(policy) == POLICY_FIELDS | {"rag"})
            and policy["purpose"] == "daily_count_schedule", "invalid_schedule_policy")
    if policy["schema_version"] == 2:
        rag = policy["rag"]
        require(isinstance(rag, dict) and set(rag) == RAG_FIELDS and type(rag["enabled"]) is bool,
                "invalid_rag_schedule_policy")
        require(relative(rag["scope_file"]).suffix == ".json" and not rag["scope_file"].startswith("scheduled/"),
                "invalid_rag_scope_path")
        require(all(isinstance(rag[key], str) and re.fullmatch(r"[0-9a-f]{64}", rag[key])
                    for key in ("scope_sha256", "runtime_sha256")), "invalid_rag_schedule_digest")
        runtime = Path(rag["runtime_config"])
        require(runtime.is_absolute() and runtime.suffix == ".json", "invalid_rag_runtime_path")
        require(type(rag["feed_valid_days"]) is int and 1 <= rag["feed_valid_days"] <= 365,
                "explicit_rag_feed_expiry_required")
    require(re.fullmatch(r"[0-9a-f]{40}", policy.get("release_sha", ""))
            and re.fullmatch(r"[0-9a-f]{64}", policy.get("seed_sha256", "")), "invalid_schedule_identity")
    seed = relative(policy["seed_checkpoint"])
    require(seed.suffix == ".json" and seed.parts[0] != "scheduled", "invalid_schedule_seed")
    require(type(policy["max_attempts"]) is int and policy["max_attempts"] == 40
            and type(policy["success_target"]) is int and policy["success_target"] == 20, "schedule_limits_changed")
    require(policy["target"] == TARGET, "schedule_target_changed")
    routes = policy["routes"]
    require(isinstance(routes, list) and 1 <= len(routes) <= 1000, "invalid_schedule_routes")
    seen = set()
    for route in routes:
        require(isinstance(route, dict) and set(route) == {"source_id", "content_type", "menu_work_id"}
                and isinstance(route["source_id"], str) and re.fullmatch(r"[a-z0-9_]{1,100}", route["source_id"])
                and route["content_type"] in {"news", "event", "goods"}
                and type(route["menu_work_id"]) is int and 0 < route["menu_work_id"] <= 2**63 - 1,
                "invalid_schedule_route")
        key = route["source_id"], route["content_type"]
        require(key not in seen, "duplicate_schedule_route")
        seen.add(key)
    return digest(policy)


def atomic_bytes(path, raw, *, mode=0o600, owner=None, immutable=False):
    path = checked_path(path, exists=False)
    if immutable and path.exists():
        require(read_bytes(path) == raw, "immutable_schedule_file_changed")
        return
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, mode)
            if owner is not None and os.name != "nt":
                os.chown(temporary, owner, owner)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path, value, **kwargs):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False).encode(), **kwargs)


@contextmanager
def exclusive_lock(path):
    """Only the host supervisor holds this lock; controller DB locks stay separate."""
    path = checked_path(path, exists=False)
    with path.open("a+b") as stream:
        os.chmod(path, 0o600)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ScheduleError("schedule_already_running") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ScheduleError("schedule_already_running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def real_runner(stage, args, *, cwd, environment, timeout):
    result = subprocess.run(args, cwd=cwd, env=environment, capture_output=True,
                            text=True, timeout=timeout)
    return CommandResult(result.returncode, result.stdout, result.stderr)


class Supervisor:
    def __init__(self, *, policy, reviewed_sha256, state_root, runtime_env, release_dir,
                 now=lambda: datetime.now(timezone.utc), runner=real_runner, owner=10001):
        self.policy = policy
        self.policy_sha = validate_policy(policy)
        require(reviewed_sha256 == self.policy_sha, "reviewed_schedule_digest_required")
        self.root = checked_path(state_root)
        self.env_path = checked_path(runtime_env)
        self.release = checked_path(release_dir)
        require(self.root.is_dir() and self.release.is_dir(), "invalid_runtime_directories")
        self.base_env = read_bytes(self.env_path, 128 * 1024)
        require(b"\x00" not in self.base_env, "invalid_runtime_environment")
        if os.name != "nt":
            require(self.env_path.stat().st_mode & 0o077 == 0, "runtime_environment_must_be_private")
        self.rag = self.policy.get("rag")
        self.compose_hashes = {name: file_sha(self.release / name)
            for name in ("compose.runtime.yaml", "compose.approved.yaml", *(RAG_COMPOSE if self.rag else ()))}
        self.now, self.runner, self.owner = now, runner, owner
        self.schedule = checked_path(self.root / "scheduled", exists=False)
        self.journal_path = self.schedule / "journal.json"
        self.rag_inputs()

    def rag_inputs(self):
        if self.rag is None:
            return None
        scope = read_json(self.host(self.rag["scope_file"]))
        require(digest(scope) == self.rag["scope_sha256"] and isinstance(scope, dict)
                and set(scope) == {"schema_version", "purpose", "routes"}
                and scope["schema_version"] == 1 and scope["purpose"] == "rag_daily_generation"
                and isinstance(scope["routes"], list) and 1 <= len(scope["routes"]) <= 200,
                "reviewed_rag_scope_changed")
        runtime = checked_path(self.rag["runtime_config"])
        require(file_sha(runtime) == self.rag["runtime_sha256"], "reviewed_rag_runtime_changed")
        config = read_json(runtime)
        require(config.get("enabled") is self.rag["enabled"] and config.get("offline") is False
                and config.get("confirm_isolated_copy") is True, "rag_runtime_activation_mismatch")
        # Scope/profile/catalog and pricing validation also run inside the
        # generation image's no-provider CLI preflight. Never read key files here.
        return scope

    def rag_expiry(self, entry):
        return (datetime.fromisoformat(entry["date"]).replace(tzinfo=KST)
                + timedelta(days=self.rag["feed_valid_days"] + 1)).isoformat()

    def rag_worker_image(self):
        entries = [line.split("=", 1)[1] for line in self.base_env.decode().splitlines()
                   if line.startswith("RAG_WORKER_IMAGE=")]
        require(len(entries) == 1, "explicit_rag_worker_image_required")
        values = shlex.split(entries[0])
        require(len(values) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", values[0]),
                "explicit_rag_worker_image_required")
        return values[0]

    def consumer_guard(self, entry, *, started):
        """Inspect local consumers without reading Env or logging raw commands.

        A disabled prepared worker subscribes to the same queue and can ACK a
        delivery. It must be absent before the active schedule starts, not merely
        have its generation flag off. This guard never stops foreign containers.
        """
        self.rag_inputs()
        self.artifacts(entry)
        expected = self.rag_worker_image()
        suffix = "after" if started else "before"
        environment = {key: value for key, value in os.environ.items()
            if not key.startswith(("DOCKER_", "COMPOSE_", "DAILY_", "ANIME_", "CONTENT_JOB_", "MAIN_POSTGRES_", "RAG_"))
            and key not in {"OPENAI_API_KEY", "LLM_KEY", "DATABASE_URL"}}
        def inspect_command(stage, args):
            try:
                return self.runner(stage, args, cwd=self.release, environment=environment, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise ScheduleError("rag_consumer_inventory_unavailable") from None
        listed = inspect_command("rag-consumers-" + suffix + "-list",
                                 ["docker", "--context", "default", "ps", "-q", "--no-trunc"])
        require(listed.returncode == 0, "rag_consumer_inventory_unavailable")
        ids = listed.stdout.split()
        require(len(ids) == len(set(ids)) and all(re.fullmatch(r"[0-9a-f]{64}", item) for item in ids),
                "rag_consumer_inventory_invalid")
        rows = []
        if ids:
            inspected = inspect_command("rag-consumers-" + suffix + "-inspect",
                ["docker", "--context", "default", "inspect", "--format", RAG_INSPECT, *ids])
            require(inspected.returncode == 0, "rag_consumer_inventory_unavailable")
            try:
                rows = [json.loads(line) for line in inspected.stdout.splitlines() if line.strip()]
            except (ValueError, TypeError):
                raise ScheduleError("rag_consumer_inventory_invalid") from None
            require(len(rows) == len(ids) and all(isinstance(row, dict) for row in rows)
                    and {row.get("id") for row in rows} == set(ids),
                    "rag_consumer_inventory_changed")
        candidates = []
        for row in rows:
            entrypoint, command = row.get("entrypoint") or [], row.get("command") or []
            require(isinstance(entrypoint, list) and isinstance(command, list)
                    and all(isinstance(value, str) for value in [*entrypoint, *command]),
                    "rag_consumer_command_unrecognized")
            args = entrypoint + command
            if row.get("running") is True and (row.get("service") == "rag-worker"
                    or any("menu_worker.rag_app" in value or "content.rag_generate" in value or "rag@" in value for value in args)):
                candidates.append((row, args))
        valid = len(candidates) == 1 if started else len(candidates) <= 1
        for row, args in candidates:
            # Only the exact reviewed Celery invocation may survive a restart.
            # Additional apps, queues, nodes or shell commands are never accepted.
            command = ["celery", "-A", "menu_worker.rag_app:app", "worker", "--concurrency=1",
                       "--queues=content.rag_generate", "--hostname=rag@scheduled", "--loglevel=INFO"]
            valid = valid and (row.get("project") == PROJECT and row.get("service") == "rag-worker"
                and row.get("name") == "/" + PROJECT + "-rag-worker-1" and row.get("working_dir") == str(self.release)
                and row.get("image") == expected and args == command)
        # Raw Docker command arguments may contain another service's secrets.
        # Persist only counts and the verification result, never stdout/stderr.
        atomic_bytes(self.host(entry["directory"] + "/rag-consumers-" + suffix + ".log"),
            json.dumps({"started": started, "rag_consumers": len(candidates), "verified": bool(valid)}, sort_keys=True).encode())
        require(valid, "rag_queue_consumer_ownership_mismatch")

    def rag_import_policy(self, entry, plan):
        return {"schema_version": 1, "purpose": "rag_pending_import", "policy_date": entry["date"],
                "day_policy_sha256": plan["day_policy_sha256"], "artifact_root": plan["config"]["artifact_root"],
                "routes": plan["rag_scope"]["routes"], "max_items": 20, "target": self.policy["target"],
                "feed_valid_until": self.rag_expiry(entry)}

    def host(self, value):
        path = self.root.joinpath(*relative(value).parts)
        return checked_path(path, exists=False)

    def container(self, value):
        return "/runtime/state/" + str(relative(value))

    def today(self):
        value = self.now()
        require(value.tzinfo is not None, "timezone_required")
        return value.astimezone(KST).date().isoformat()

    def load_journal(self):
        require(file_sha(self.host(self.policy["seed_checkpoint"])) == self.policy["seed_sha256"], "schedule_seed_changed")
        if not self.journal_path.exists():
            return {"schema_version": 1, "policy_sha256": self.policy_sha,
                    "runtime_environment_sha256": hashlib.sha256(self.base_env).hexdigest(),
                    "compose_sha256": self.compose_hashes, "days": []}
        journal = read_json(self.journal_path)
        require(journal.get("schema_version") == 1 and journal.get("policy_sha256") == self.policy_sha
                and journal.get("runtime_environment_sha256") == hashlib.sha256(self.base_env).hexdigest()
                and journal.get("compose_sha256") == self.compose_hashes, "schedule_configuration_changed")
        require(isinstance(journal.get("days"), list), "invalid_schedule_journal")
        source, sha, last = self.policy["seed_checkpoint"], self.policy["seed_sha256"], ""
        for number, entry in enumerate(journal["days"]):
            day = entry.get("date", "")
            require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day > last
                    and entry.get("source") == source and entry.get("source_sha256") == sha,
                    "schedule_lineage_changed")
            require(entry.get("checkpoint") == str(PurePosixPath(source).parent / ("daily-" + day + ".json"))
                    and entry.get("directory") == "scheduled/" + day, "schedule_day_paths_changed")
            require(entry.get("phase") in {"preparing", "prepared", "generating", "importing", "complete", "blocked", "reconciled_paused"},
                    "invalid_schedule_phase")
            recoveries = entry.get("failure_recoveries", [])
            require(isinstance(recoveries, list), "invalid_schedule_recovery_history")
            for recovery in recoveries:
                require(isinstance(recovery, dict)
                        and re.fullmatch(r"[0-9a-f]{64}", recovery.get("receipt_sha256", "")),
                        "invalid_schedule_recovery_history")
                relative(recovery.get("receipt", ""))
            require(entry["phase"] != "reconciled_paused" or recoveries, "missing_schedule_recovery_history")
            require(number == len(journal["days"]) - 1 or entry["phase"] == "complete", "unfinished_schedule_ancestor")
            if entry["phase"] == "complete":
                require(file_sha(self.host(entry["checkpoint"])) == entry.get("checkpoint_sha256"),
                        "completed_day_checkpoint_changed")
                source, sha = entry["checkpoint"], entry["checkpoint_sha256"]
            last = day
        return journal

    def status(self):
        journal = self.load_journal()
        last = journal["days"][-1] if journal["days"] else None
        return {"mode": "status", "policy_sha256": self.policy_sha, "today": self.today(),
                "last_date": last["date"] if last else None, "phase": last["phase"] if last else "not_started",
                "max_attempts": 40, "success_target": 20, "generation_enabled": False,
                "llm_calls": 0, "queue_writes": 0, "service_db_writes": 0}

    def save(self, journal):
        save_json(self.journal_path, journal)

    def mkdir(self, path, *, producer=False):
        checked_path(path, exists=False).mkdir(mode=0o750, parents=False, exist_ok=True)
        if producer and self.owner is not None and os.name != "nt":
            os.chown(path, self.owner, self.owner)
        os.chmod(path, 0o750 if producer else 0o700)

    def env_bytes(self, entry, plan_sha="0" * 64, import_sha="0" * 64):
        values = {
            "DAILY_RELEASE_TAG": self.policy["release_sha"],
            "DAILY_SOURCE_CHECKPOINT": self.container(entry["source"]),
            "DAILY_SOURCE_SHA256": entry["source_sha256"],
            "DAILY_CHECKPOINT": self.container(entry["checkpoint"]),
            "DAILY_POLICY_DATE": entry["date"], "DAILY_PLAN_SHA256": plan_sha,
            "DAILY_INBOX_NAME": entry["directory"] + "/inbox",
            "DAILY_IMPORT_POLICY_HOST_FILE": str(self.host(entry["directory"] + "/import-policy.json")),
            "DAILY_IMPORT_POLICY_SHA256": import_sha,
        }
        if self.rag:
            values.update(RAG_GENERATION_ENABLED="true" if self.rag["enabled"] else "false",
                RAG_RUNTIME_CONFIG_HOST_FILE=self.rag["runtime_config"],
                RAG_INBOX_NAME=entry["directory"] + "/rag-inbox",
                RAG_IMPORT_POLICY_HOST_FILE=str(self.host(entry["directory"] + "/rag-import-policy.json")),
                RAG_IMPORT_POLICY_SHA256=entry.get("rag_import_policy_sha256", "0" * 64),
                RAG_FEED_VALID_UNTIL=self.rag_expiry(entry))
        require(all("\n" not in value and "\r" not in value and "'" not in value for value in values.values()),
                "invalid_derived_environment")
        return self.base_env.rstrip(b"\r\n") + b"\n" + "".join(f"{key}='{value}'\n" for key, value in values.items()).encode()

    def command(self, stage, entry, args, *, planning=False, timeout=90000):
        require(validate_policy(self.policy) == self.policy_sha
                and read_bytes(self.env_path, 128 * 1024) == self.base_env
                and all(file_sha(self.release / name) == value for name, value in self.compose_hashes.items()),
                "schedule_configuration_changed")
        self.rag_inputs()
        if stage != "plan":
            self.artifacts(entry)
        env_file = self.host(entry["directory"] + ("/planning.env" if planning else "/runtime.env"))
        compose_files = ["compose.runtime.yaml", "compose.approved.yaml"]
        if self.rag:
            compose_files.append(RAG_COMPOSE[0])
            if self.rag["enabled"]:
                compose_files.append(RAG_COMPOSE[1])
        command = ["docker", "--context", "default", "compose", "--project-name", PROJECT, "--env-file", str(env_file),
                   "--profile", "manual", *[item for name in compose_files for item in ("-f", str(self.release / name))], *args]
        environment = {key: value for key, value in os.environ.items()
            if not key.startswith(("DOCKER_", "COMPOSE_", "DAILY_", "ANIME_", "CONTENT_JOB_", "MAIN_POSTGRES_", "RAG_"))}
        result = self.runner(stage, command, cwd=self.release, environment=environment, timeout=timeout)
        atomic_bytes(self.host(entry["directory"] + "/" + stage + ".log"),
                     (result.stdout + "\n" + result.stderr).encode())
        return result

    def daily_args(self, entry):
        args = ["--registered-routes", "--source-checkpoint", self.container(entry["source"]),
                "--source-sha256", entry["source_sha256"], "--checkpoint", self.container(entry["checkpoint"]),
                "--policy-date", entry["date"], "--delivery-root", self.container(entry["directory"] + "/inbox")]
        if self.rag:
            args += ["--rag-scope", self.container(self.rag["scope_file"]),
                "--expected-rag-scope-sha256", self.rag["scope_sha256"],
                "--rag-delivery-root", self.container(entry["directory"] + "/rag-inbox"),
                "--rag-runtime-config", RAG_RUNTIME, "--expected-rag-runtime-sha256", self.rag["runtime_sha256"]]
        return args

    def json_output(self, result, code):
        require(result.returncode == 0, code)
        try:
            value = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ScheduleError(code) from None
        require(isinstance(value, dict), code)
        return value

    def validate_plan(self, plan, entry):
        policy = plan.get("daily_policy", {})
        config = plan.get("config", {})
        require(policy.get("schema_version") == 3 and policy.get("limit_mode") == "daily_counts"
                and policy.get("routes") == self.policy["routes"] and policy.get("max_attempts") == 40
                and policy.get("success_target") == 20 and "daily_budget_usd" not in policy,
                "generated_plan_outside_schedule")
        require(policy.get("policy_date") == entry["date"]
                and policy.get("delivery_root") == self.container(entry["directory"] + "/inbox")
                and plan.get("source_checkpoint") == self.container(entry["source"])
                and plan.get("source_sha256") == entry["source_sha256"]
                and plan.get("checkpoint") == self.container(entry["checkpoint"])
                and plan.get("day_policy_sha256") == digest(policy), "generated_plan_identity_changed")
        end = datetime.fromisoformat(entry["date"]).replace(tzinfo=KST) + timedelta(days=1)
        require(config.get("limit_mode") == "daily_counts" and config.get("total_budget_usd") is None
                and policy.get("deadline") == config.get("deadline") == end.isoformat()
                and config.get("job_timeout_seconds") == 1500 and plan.get("generation_enabled") is False,
                "generated_plan_limits_changed")
        if self.rag:
            require(plan.get("rag_scope") == self.rag_inputs()
                    and plan.get("rag_scope_sha256") == self.rag["scope_sha256"]
                    and plan.get("rag_runtime_sha256") == self.rag["runtime_sha256"]
                    and plan.get("rag_delivery_root") == self.container(entry["directory"] + "/rag-inbox"),
                    "generated_rag_plan_changed")
        else:
            require(not any(key.startswith("rag_") for key in plan), "unexpected_rag_schedule_scope")

    def prepare(self, journal, entry):
        directory = self.host(entry["directory"])
        self.mkdir(directory)
        os.chmod(directory, 0o755)
        self.mkdir(directory / "inbox", producer=True)
        if self.rag:
            self.mkdir(directory / "rag-inbox", producer=True)
        atomic_bytes(directory / "planning.env", self.env_bytes(entry), immutable=True)
        result = self.command("plan", entry, ["run", "--rm", "--no-deps", "-T", "daily-controller", *self.daily_args(entry)], planning=True)
        output = self.json_output(result, "day_plan_failed")
        plan = output.get("plan", {})
        self.validate_plan(plan, entry)
        require(output.get("plan_sha256") == digest(plan), "generated_plan_digest_changed")
        save_json(directory / "plan.json", plan, immutable=True)
        import_policy = {"schema_version": 2, "purpose": "pending_content_import",
            "run_id": plan["config"]["run_id"], "policy_date": entry["date"], "routes": self.policy["routes"],
            "max_items": 20, "bundle_root": plan["config"]["bundle_root"],
            "day_policy_sha256": plan["day_policy_sha256"], "target": self.policy["target"]}
        save_json(directory / "import-policy.json", import_policy, mode=0o440, owner=self.owner, immutable=True)
        entry.update(plan_sha256=digest(plan), import_policy_sha256=digest(import_policy))
        if self.rag:
            rag_import = self.rag_import_policy(entry, plan)
            save_json(directory / "rag-import-policy.json", rag_import, mode=0o440, owner=self.owner, immutable=True)
            entry["rag_import_policy_sha256"] = digest(rag_import)
        atomic_bytes(directory / "runtime.env", self.env_bytes(entry, entry["plan_sha256"], entry["import_policy_sha256"]), immutable=True)
        entry["phase"] = "prepared"
        self.save(journal)

    def artifacts(self, entry):
        plan = read_json(self.host(entry["directory"] + "/plan.json"))
        self.validate_plan(plan, entry)
        require(digest(plan) == entry["plan_sha256"], "saved_day_plan_changed")
        import_policy = read_json(self.host(entry["directory"] + "/import-policy.json"))
        require(digest(import_policy) == entry["import_policy_sha256"], "saved_import_policy_changed")
        if self.rag:
            rag_import = read_json(self.host(entry["directory"] + "/rag-import-policy.json"))
            require(rag_import == self.rag_import_policy(entry, plan)
                    and digest(rag_import) == entry.get("rag_import_policy_sha256"), "saved_rag_import_policy_changed")
        require(read_bytes(self.host(entry["directory"] + "/runtime.env")) ==
                self.env_bytes(entry, entry["plan_sha256"], entry["import_policy_sha256"]), "saved_runtime_environment_changed")
        return plan

    def recovery_reference(self, receipt, reviewed_sha256):
        require(isinstance(reviewed_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", reviewed_sha256),
                "reviewed_recovery_digest_required")
        path = checked_path(receipt)
        require(path.is_relative_to(self.root), "recovery_receipt_outside_state")
        name = path.relative_to(self.root).as_posix()
        relative(name)
        require(digest(read_json(path)) == reviewed_sha256, "reviewed_recovery_digest_changed")
        return {"receipt": name, "receipt_sha256": reviewed_sha256}

    def audit_recovery(self, entry, reference, *, resume=False):
        plan = self.artifacts(entry)
        # This is an application audit, with the same file/DB locks as the
        # controller. It reads evidence and broker state, never publishes work.
        require(digest(read_json(self.host(reference["receipt"]))) == reference["receipt_sha256"],
                "reviewed_recovery_digest_changed")
        report = self.json_output(self.command("recovery-audit", entry,
            ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "daily-controller", "-c",
             MIXED_RECOVERY_CHECK if self.rag else RECOVERY_CHECK,
             json.dumps(plan, separators=(",", ":")), entry["plan_sha256"],
             self.container(reference["receipt"]), reference["receipt_sha256"], "resume" if resume else "reconcile"], timeout=180),
             "failure_recovery_audit_failed")
        require(report.get("verified") is True and report.get("generation_enabled") is False
                and report.get("receipt_sha256") == reference["receipt_sha256"]
                and type(report.get("generation_window_open")) is bool, "invalid_failure_recovery_audit")
        return report

    def reconcile(self, journal, entry, reference):
        require(entry["phase"] in {"blocked", "reconciled_paused"}, "recovery_requires_blocked_day")
        child = read_json(self.host(entry["checkpoint"]))
        require(child.get("status") == "stopped" and child.get("stop_reason") == "failure_reconciled_paused",
                "checkpoint_requires_paused_recovery")
        recoveries = entry.get("failure_recoveries", [])
        if entry["phase"] == "reconciled_paused":
            require(all(recoveries[-1].get(key) == value for key, value in reference.items()),
                    "scheduled_recovery_changed")
        else:
            require(not any(row.get("receipt_sha256") == reference["receipt_sha256"] for row in recoveries),
                    "failure_recovery_already_recorded")
        self.audit_recovery(entry, reference)
        if entry["phase"] != "reconciled_paused":
            entry.setdefault("failure_recoveries", []).append({**reference, "reconciled_at": self.now().isoformat(),
                "blocked_reason": entry.get("blocked_reason")})
            entry["phase"] = "reconciled_paused"
            self.save(journal)
        return {"mode": "reconcile", "status": "reconciled_paused", "policy_date": entry["date"],
                "receipt_sha256": reference["receipt_sha256"], "host_state_writes": True,
                "job_checkpoint_writes": 0, "queue_writes": 0, "service_db_writes": 0,
                "generation_enabled": False, "llm_calls": 0, "publication_enabled": False}

    def terminal_checkpoint(self, entry, plan):
        child = read_json(self.host(entry["checkpoint"]))
        parent = read_json(self.host(entry["source"]))
        require(file_sha(self.host(entry["source"])) == entry["source_sha256"], "day_parent_changed")
        require(child.get("config") == plan["config"] and child.get("daily_policy") == plan["daily_policy"]
                and child.get("daily_plan_sha256") == entry["plan_sha256"], "day_checkpoint_identity_changed")
        if self.rag:
            require(child.get("rag_scope") == plan["rag_scope"]
                    and child.get("rag_scope_sha256") == plan["rag_scope_sha256"], "day_rag_scope_changed")
        else:
            require(not child.get("rag_scope") and not any(row.get("producer") == "ani_rag"
                    for row in child.get("records", [])), "unexpected_rag_schedule_scope")
        require(child.get("records", [])[:len(parent["records"])] == parent["records"]
                and child.get("seen", [])[:len(parent["seen"])] == parent["seen"]
                and child.get("accounting_resume_audit", []) == parent.get("accounting_resume_audit", []),
                "day_history_changed")
        for key in ("failure_recovery_audit", "failure_recovery_resume_audit"):
            inherited = parent.get(key, [])
            require(child.get(key, [])[:len(inherited)] == inherited, "day_recovery_history_changed")
        require(all(row.get("state") in {"complete", "duplicate"} for row in child["records"]), "day_has_unfinished_work")
        require(child.get("status") == "complete" and child.get("stop_reason") in COMPLETIONS
                or child.get("status") == "stopped" and child.get("stop_reason") in SAFE_STOPS,
                "day_requires_reconciliation")
        today = child["records"][len(parent["records"]):]
        attempts = sum(bool(row.get("counts_slot")) for row in today)
        successes = {(row["request"]["source_id"], row["canonical_url"]) for row in today
            if row.get("counts_slot") and row.get("execution_status") == "succeeded" and row.get("outcome") == "ready_for_staging"}
        require(attempts <= 40 and len(successes) <= 20, "day_limits_exceeded")
        if child.get("status") == "complete":
            require(child["stop_reason"] == "daily_success_target_reached" and len(successes) == 20
                    or child["stop_reason"] == "daily_attempt_limit_reached" and attempts == 40,
                    "day_completion_count_mismatch")
        return child, attempts, len(successes)

    def delivery_counts(self, entry, child):
        parent = read_json(self.host(entry["source"]))
        today = child["records"][len(parent["records"]):]
        def successes(rag):
            return {(row["request"]["source_id"], row["canonical_url"]) for row in today
                if row.get("counts_slot") and row.get("execution_status") == "succeeded"
                and row.get("outcome") == "ready_for_staging" and (row.get("producer") == "ani_rag") is rag}
        return len(successes(False)), len(successes(True))

    def verify_import(self, report, expected, code):
        require(report.get("publication_enabled") is False and report.get("operator_approvals_created") == 0
                and report.get("llm_calls") == 0 and type(report.get("created")) is int
                and type(report.get("unchanged")) is int and report["created"] >= 0 and report["unchanged"] >= 0
                and report["created"] + report["unchanged"] == expected, code)

    def execute(self, journal, entry, *, recovery=None):
        plan = self.artifacts(entry)
        child_path = self.host(entry["checkpoint"])
        recovery_window_open = True
        unstarted_preflight_verified = False
        if recovery is not None:
            require(entry["phase"] in {"reconciled_paused", "importing"}, "recovery_requires_paused_schedule")
            report = self.audit_recovery(entry, recovery, resume=True)
            recovery_window_open = report["generation_window_open"]
            child = read_json(child_path)
            if child.get("status") == "complete" or child.get("status") == "stopped" and child.get("stop_reason") in SAFE_STOPS:
                self.terminal_checkpoint(entry, plan)
                entry["phase"] = "importing"
                self.save(journal)
            else:
                require(child.get("status") == "running" or child.get("status") == "stopped"
                        and child.get("stop_reason") == "failure_reconciled_paused", "day_requires_reconciliation")
        if entry["phase"] in {"prepared", "generating", "reconciled_paused"}:
            if recovery is not None:
                pass  # Full checkpoint/DB/broker audit above precedes Worker startup.
            elif not child_path.exists():
                report = self.json_output(self.command("preflight", entry,
                    ["run", "--rm", "--no-deps", "-T", "daily-controller", *self.daily_args(entry),
                     "--expected-plan-sha256", entry["plan_sha256"], "--preflight"]), "day_preflight_failed")
                require(report.get("verified") is True and report.get("generation_enabled") is False, "invalid_day_preflight")
                unstarted_preflight_verified = entry["phase"] == "prepared" and not child_path.exists()
            else:
                child = read_json(child_path)
                require(child.get("status") in {"running", "complete"}
                        or child.get("status") == "stopped" and child.get("stop_reason") in SAFE_STOPS,
                        "day_requires_reconciliation")
                report = self.json_output(self.command("resume-audit", entry,
                    ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "daily-controller", "-c",
                     MIXED_RESUME_CHECK if self.rag else RESUME_CHECK,
                     json.dumps(plan, separators=(",", ":")), entry["plan_sha256"]], timeout=180), "resume_history_audit_failed")
                require(report.get("verified") is True and report.get("generation_enabled") is False,
                        "invalid_resume_audit")
                if child["status"] in {"complete", "stopped"}:
                    self.terminal_checkpoint(entry, plan)
                    entry["phase"] = "importing"
                    self.save(journal)
            if entry["phase"] != "importing":
                if recovery_window_open and self.rag:
                    try:
                        self.consumer_guard(entry, started=False)
                    except ScheduleError as error:
                        if (unstarted_preflight_verified and recovery is None and entry["phase"] == "prepared"
                                and not child_path.exists()):
                            # No worker-start or generation stage has been
                            # attempted for this unchanged prepared day. A
                            # foreign consumer is an activation prerequisite,
                            # not a failed generation needing a job receipt.
                            raise ActivationPreflightError(str(error)) from None
                        raise
                # Recovered runs stay paused in the host journal until terminal.
                # A process crash therefore needs the same explicit resume again.
                if recovery is None:
                    entry["phase"] = "generating"
                else:
                    entry["failure_recoveries"][-1].setdefault("resume_requested_at", self.now().isoformat())
                self.save(journal)
                if recovery_window_open:
                    require(self.command("worker-start", entry, ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120",
                        "generation-worker", *(["rag-worker"] if self.rag else [])], timeout=180).returncode == 0,
                            "generation_worker_start_failed")
                    if self.rag:
                        self.consumer_guard(entry, started=True)
                result = self.command("generate", entry,
                    ["run", "--rm", "--no-deps", "-T", "daily-controller", *self.daily_args(entry),
                     "--expected-plan-sha256", entry["plan_sha256"], "--apply",
                     *(["--resume-failure-recovery", recovery["receipt_sha256"]] if recovery else [])])
                require(result.returncode in {0, 2}, "day_generation_command_failed")
            self.terminal_checkpoint(entry, plan)
            entry["phase"] = "importing"
            self.save(journal)
        require(entry["phase"] == "importing", "invalid_execution_phase")
        child, attempts, successes = self.terminal_checkpoint(entry, plan)
        period_successes, rag_successes = self.delivery_counts(entry, child)
        require(period_successes + rag_successes == successes, "day_delivery_count_mismatch")
        require(self.today() == entry["date"] or recovery is not None, "previous_day_requires_reconciliation")
        self.json_output(self.command("broker-empty", entry,
            ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "daily-controller", "-c",
             MIXED_BROKER_CHECK if self.rag else BROKER_CHECK], timeout=60),
            "generation_broker_not_empty")
        require(self.command("worker-stop", entry, ["stop", "-t", "60", "generation-worker",
            *(["rag-worker"] if self.rag else [])], timeout=90).returncode == 0,
                "generation_worker_stop_failed")
        report = self.json_output(self.command("import", entry,
            ["run", "--rm", "--no-deps", "-T", "pending-importer", "--policy", "/runtime/import-policy.json",
             "--reviewed-policy-sha256", entry["import_policy_sha256"], "--inbox", "/runtime/inbox", "--once", "--apply",
             *(["--reconcile-policy-date", entry["date"]] if recovery is not None and entry["date"] < self.today() else [])]),
            "pending_import_failed")
        self.verify_import(report, period_successes, "pending_import_not_drained")
        if self.rag:
            rag_report = self.json_output(self.command("rag-import", entry,
                ["run", "--rm", "--no-deps", "-T", "rag-pending-importer",
                 "--policy", "/runtime/rag-import-policy.json", "--reviewed-policy-sha256", entry["rag_import_policy_sha256"],
                 "--inbox", self.container(entry["directory"] + "/rag-inbox"),
                 "--feed-valid-until", self.rag_expiry(entry), "--database-url-file", "/run/secrets/rag-service-dsn",
                 "--deployment-env", self.policy["target"]["environment"], "--apply",
                 *(["--reconcile-policy-date", entry["date"]] if recovery is not None and entry["date"] < self.today() else [])]),
                 "rag_pending_import_failed")
            self.verify_import(rag_report, rag_successes, "rag_pending_import_not_drained")
        entry.update(phase="complete", checkpoint_sha256=file_sha(child_path), attempts=attempts,
                     successes=successes, imported=successes, completed_at=self.now().isoformat())
        if self.rag:
            entry.update(period_imported=period_successes, rag_imported=rag_successes)
        self.save(journal)

    def run(self, *, apply=False, preflight=False, reconcile=False, resume_reconciled=False,
            recovery_receipt=None, reviewed_recovery_sha256=None):
        require(sum(map(bool, (apply, preflight, reconcile, resume_reconciled))) <= 1, "choose_one_schedule_mode")
        recovery_mode = reconcile or resume_reconciled
        if self.rag and (apply or resume_reconciled):
            # A disabled, reviewed preflight is preparation only. It never
            # grants implicit authority to start either producer or import.
            require(self.rag["enabled"] is True, "rag_schedule_disabled")
            self.rag_inputs()
        require(recovery_mode or recovery_receipt is None and reviewed_recovery_sha256 is None,
                "recovery_arguments_require_explicit_mode")
        reference = self.recovery_reference(recovery_receipt, reviewed_recovery_sha256) if recovery_mode else None
        if not any((apply, preflight, recovery_mode)):
            return self.status()
        # A competing timer must not temporarily restrict producer traversal
        # before it discovers that another supervisor holds the lock.
        checked_path(self.schedule, exists=False).mkdir(mode=0o755, exist_ok=True)
        # Producer UID traverses this directory to reach its per-day inbox;
        # journal/environment files themselves remain root-only (0600).
        os.chmod(self.schedule, 0o755)
        with exclusive_lock(self.root / "daily-schedule.lock"):
            journal = self.load_journal()
            today = self.today()
            last = journal["days"][-1] if journal["days"] else None
            if recovery_mode:
                require(last is not None, "recovery_requires_existing_day")
                require(last["date"] <= today, "schedule_clock_moved_backwards")
                if reconcile:
                    return self.reconcile(journal, last, reference)
                history = last.get("failure_recoveries", [])
                require(history and all(history[-1].get(key) == value for key, value in reference.items()),
                        "scheduled_recovery_changed")
                if last["phase"] == "complete":
                    return {"mode": "resume-reconciled", "status": "already_complete", "policy_date": last["date"],
                            "attempts": last["attempts"], "successes": last["successes"], "generation_enabled": False,
                            "publication_enabled": False}
                require(last["phase"] in {"reconciled_paused", "importing"}, "recovery_requires_paused_schedule")
                try:
                    self.execute(journal, last, recovery=reference)
                except ScheduleError as error:
                    # A new failed job needs its own receipt. Keep every earlier
                    # recovery decision and never convert it into timer approval.
                    try:
                        child = read_json(self.host(last["checkpoint"]))
                        safe = (child.get("status") in {"running", "complete"} or child.get("status") == "stopped"
                                and child.get("stop_reason") in SAFE_STOPS | {"failure_reconciled_paused"})
                    except (ScheduleError, OSError):
                        safe = False
                    last.update(phase="reconciled_paused" if safe else "blocked", blocked_reason=str(error))
                    self.save(journal)
                    raise
                return {"mode": "resume-reconciled", "status": "complete", "policy_date": last["date"],
                        "attempts": last["attempts"], "successes": last["successes"], "publication_enabled": False}
            if last:
                require(last["date"] <= today, "schedule_clock_moved_backwards")
                require(last["phase"] != "blocked", "schedule_requires_reconciliation")
                require(last["phase"] != "reconciled_paused"
                        and (not last.get("failure_recoveries") or last["phase"] == "complete"),
                        "explicit_failure_resume_required")
                if last["date"] == today and last["phase"] == "complete":
                    return {"mode": "preflight" if preflight else "apply", "status": "already_complete", "policy_date": today,
                            "attempts": last["attempts"], "successes": last["successes"], "publication_enabled": False,
                            "generation_enabled": False}
                require(last["date"] == today or last["phase"] == "complete", "previous_day_requires_reconciliation")
            if not last or last["date"] < today:
                source = last["checkpoint"] if last else self.policy["seed_checkpoint"]
                sha = last["checkpoint_sha256"] if last else self.policy["seed_sha256"]
                entry = {"date": today, "source": source, "source_sha256": sha,
                    "checkpoint": str(PurePosixPath(source).parent / ("daily-" + today + ".json")),
                    "directory": "scheduled/" + today, "phase": "preparing"}
                require(not self.host(entry["checkpoint"]).exists(), "unowned_daily_checkpoint_exists")
                journal["days"].append(entry)
                self.save(journal)
            else:
                entry = last
            if preflight:
                require(entry["phase"] in {"preparing", "prepared"}, "preflight_requires_unstarted_day")
            try:
                if entry["phase"] == "preparing":
                    self.prepare(journal, entry)
                if preflight:
                    self.artifacts(entry)
                    report = self.json_output(self.command("preflight", entry,
                        ["run", "--rm", "--no-deps", "-T", "daily-controller", *self.daily_args(entry),
                         "--expected-plan-sha256", entry["plan_sha256"], "--preflight"]), "day_preflight_failed")
                    require(report.get("verified") is True and report.get("generation_enabled") is False,
                            "invalid_day_preflight")
                    return {"mode": "preflight", "status": "prepared", "policy_date": today,
                            "plan_sha256": entry["plan_sha256"], "verified": True, "host_state_writes": True,
                            "job_checkpoint_writes": 0, "queue_writes": 0, "service_db_writes": 0,
                            "generation_enabled": False, "llm_calls": 0, "publication_enabled": False}
                self.execute(journal, entry)
            except ScheduleError as error:
                if isinstance(error, ActivationPreflightError):
                    require(entry["phase"] == "prepared" and not self.host(entry["checkpoint"]).exists(),
                            "activation_preflight_state_changed")
                    raise
                entry.update(phase="blocked", blocked_reason=str(error))
                self.save(journal)
                raise
            return {"mode": "apply", "status": "complete", "policy_date": today,
                    "attempts": entry["attempts"], "successes": entry["successes"], "publication_enabled": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--reviewed-policy-sha256", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--preflight", action="store_true", help="Prepare immutable host day files and audit jobs/broker; never starts generation or imports")
    mode.add_argument("--reconcile", action="store_true", help="Audit an applied failure receipt and pause the host journal; no generation or import")
    mode.add_argument("--resume-reconciled", action="store_true", help="Explicit continuation of a pinned recovery, or pending-only finalization after its day ends")
    parser.add_argument("--recovery-receipt", type=Path)
    parser.add_argument("--reviewed-recovery-sha256")
    args = parser.parse_args(argv)
    try:
        supervisor = Supervisor(policy=read_json(args.policy), reviewed_sha256=args.reviewed_policy_sha256,
            state_root=args.state_root, runtime_env=args.runtime_env, release_dir=args.release_dir)
        print(json.dumps(supervisor.run(apply=args.apply, preflight=args.preflight, reconcile=args.reconcile,
            resume_reconciled=args.resume_reconciled, recovery_receipt=args.recovery_receipt,
            reviewed_recovery_sha256=args.reviewed_recovery_sha256), ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, ScheduleError) else "schedule_failed_inspect_private_logs",
                          "error_type": type(error).__name__, "publication_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
