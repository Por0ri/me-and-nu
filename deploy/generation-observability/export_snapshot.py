"""Host-only read-only generation observer; the sole write is a sanitized snapshot.

No provider imports, queue publication, Celery control broadcasts, service DB,
controller locks, recovery actions or Docker state changes are performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from uuid import UUID

KST = timezone(timedelta(hours=9))
STATE = Path('/home/ubuntu/me-nu-beta-20261116/state')
APPS = Path('/home/ubuntu/apps')
BINDING = Path('/etc/me-nu-daily-schedule.env')
JOBS = 'menu-beta-generation-20261116-jobs-db-1'
REDIS = 'menu-beta-generation-20261116-generation-redis-1'
DOCKER = ['docker', '--context', 'default']
STATUSES = ('queued', 'running', 'succeeded', 'failed', 'needs_attention', 'cancelled')
OUTCOMES = ('held', 'skipped', 'ready_for_staging', 'rejected')
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 20000


class ObservationError(RuntimeError):
    pass


class SourceChanged(ObservationError):
    pass


def require(value):
    if not value:
        raise ObservationError('invalid_observation_source')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def money(value):
    # Worker Decimal serialization legitimately emits 0E-8 / 1E-8. Accept the
    # bounded source value, then emit canonical fixed-point strings to the API.
    require(isinstance(value, str) and len(value) <= 48)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ObservationError('invalid_cost_source') from None
    require(result.is_finite() and not result.is_signed() and 0 <= result < Decimal('1000000000000')
        and result.as_tuple().exponent >= -12 and result.as_tuple().exponent <= 12)
    return result


def amount(value):
    result = format(value, 'f')
    money(result)
    return result


def parse(raw):
    def unique(pairs):
        row = dict(pairs)
        require(len(row) == len(pairs))
        return row
    require(len(raw) <= MAX_BYTES)
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ObservationError('invalid_json')))


def integer(value, maximum=MAX_RECORDS):
    require(type(value) is int and 0 <= value <= maximum)
    return value


def daily_limits():
    """Reuse the sealed stdlib-only Worker validator without importing Worker."""
    path = Path(__file__).with_name('daily_limits.py')
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / 'worker/menu_worker/daily_limits.py'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES)
    spec = importlib.util.spec_from_file_location('generation_observation_daily_limits', path)
    require(spec is not None)
    module = importlib.util.module_from_spec(spec)
    # Do not write bytecode: the exporter's only write is generation.json.
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def schedule_attempt_limit(policy):
    require(isinstance(policy, dict) and 'max_attempts' in policy)
    value, schema = policy['max_attempts'], policy.get('schema_version')
    require(type(schema) is int and (schema == 3 and value is None
        or schema in {1, 2} and type(value) is int and value == 40))
    require(type(policy.get('success_target')) is int and policy['success_target'] == 20)
    return value


def identity(row):
    request = row.get('request', {})
    url = row.get('canonical_url')
    require(isinstance(url, str) and 0 < len(url) <= 4096)
    if row.get('producer') == 'ani_rag':
        require(re.fullmatch(r'urn:menu-rag:[0-9a-f]{64}', url))
        return 'ani_rag', url
    require(isinstance(request.get('source_id'), str) and request['source_id'])
    return request['source_id'], url


def receipt_partial(row, load_receipt):
    reference = row.get('failure_recovery_receipt')
    require(isinstance(reference, dict) and set(reference) == {'receipt_path', 'receipt_sha256'})
    receipt = load_receipt(reference['receipt_path'])
    require(digest(receipt) == reference['receipt_sha256'] and receipt.get('unknown_total') is True
        and receipt.get('job', {}).get('job_id') == row.get('job_id')
        and receipt.get('job', {}).get('generation_attempt_id') == row.get('generation_attempt_id')
        and receipt.get('original_record', {}).get('counts_slot') is True)
    value = receipt.get('known_partial_estimated_usd')
    return money(value) if value is not None else Decimal(0)


def costs(rows, load_receipt):
    known = reserved = Decimal(0)
    unknown = pending = 0
    for row in rows:
        require(type(row.get('counts_slot')) is bool and row.get('state') in {'reserved', 'complete', 'duplicate'})
        if not row['counts_slot']:
            require(row['state'] == 'duplicate' and row.get('estimated_cost_usd') is None)
            continue
        incomplete = row.get('state') != 'complete'
        pending += incomplete
        measured = row.get('estimated_cost_usd')
        partial = row.get('rag_known_partial_usd')
        method = row.get('accounting_method')
        require(method in {None, 'unknown', 'reserve'})
        is_unknown = bool(row.get('cost_unknown') or row.get('rag_cost_incomplete'))
        if method == 'unknown':
            require(measured is None and partial is None)
            known += receipt_partial(row, load_receipt)
            is_unknown = True
        elif method == 'reserve':
            require(measured is None and partial is None)
            evidence = row.get('accounting_reservation', {})
            original = {key: value for key, value in evidence.items() if key != 'receipt_sha256'}
            require(evidence.get('job_id') == row.get('job_id')
                and evidence.get('receipt_sha256') == digest({'schema_version': 1, 'records': [original]})
                and money(evidence.get('reserved_usd')) == money(row.get('reserved_cost_usd')))
            reserved += money(row['reserved_cost_usd'])
            is_unknown = True
        elif measured is not None:
            require(partial is None and not is_unknown)
            known += money(measured)
        elif partial is not None:
            require(row.get('producer') == 'ani_rag' and row.get('rag_cost_incomplete') is True)
            known += money(partial)
            is_unknown = True
        elif not incomplete:
            is_unknown = True
        unknown += is_unknown
    return {'known': known, 'reserved': reserved, 'unknown': unknown, 'pending': pending}


def account(state, *, now, load_receipt):
    """Count durable reservations, not delivery/retry/attempt database rows."""
    records = state.get('records')
    require(isinstance(records, list) and len(records) <= MAX_RECORDS)
    policy, config = state.get('daily_policy', {}), state.get('config', {})
    require(state.get('daily_policies', [None])[-1] == policy and policy.get('schema_version') in {3, 4}
        and policy.get('limit_mode') == config.get('limit_mode') == 'daily_counts'
        and 'daily_budget_usd' not in policy and config.get('total_budget_usd', 'missing') is None)
    limits = daily_limits()
    try:
        max_attempts = limits.effective_daily_attempt_limit(state)
    except limits.DailyLimitError:
        raise ObservationError('invalid_daily_limit_source') from None
    day = datetime.strptime(policy['policy_date'], '%Y-%m-%d').date()
    deadline = (datetime.combine(day, datetime.min.time(), KST) + timedelta(days=1)).isoformat()
    require(policy.get('deadline') == config.get('deadline') == deadline)
    start = integer(policy.get('start_record_index'), len(records))
    require(policy.get('prior_records_sha256') == digest(records[:start])
        and policy.get('prior_slots') == sum(row.get('counts_slot') is True for row in records[:start]))
    prefix = costs(records[:start], load_receipt)
    baseline = money(config.get('baseline_estimated_cost_usd'))
    require(money(policy.get('prior_accounted_usd')) == baseline + prefix['known'] + prefix['reserved'])
    require(policy.get('prior_unknown_costs', []) == [
        {'job_id': row['job_id'], **row['failure_recovery_receipt']}
        for row in records[:start] if row.get('accounting_method') == 'unknown'])
    today = records[start:]
    require(all(row.get('policy_date') == day.isoformat() and row.get('day_policy_sha256') == digest(policy) for row in today))
    job_ids = [row['job_id'] for row in records if row.get('counts_slot') and row.get('job_id')]
    require(len(job_ids) == len(set(job_ids)))
    for job_id in job_ids:
        require(str(UUID(job_id)) == job_id)
    current, lifetime = costs(today, load_receipt), costs(records, load_receipt)
    attempts = sum(row['counts_slot'] for row in today)
    passed = {identity(row) for row in today if row['counts_slot'] and row['state'] == 'complete'
        and row.get('execution_status') == 'succeeded' and row.get('outcome') == 'ready_for_staging'}
    require((max_attempts is None or attempts <= max_attempts) and len(passed) <= 20)
    rag = sum(row['counts_slot'] and row.get('producer') == 'ani_rag' for row in today)
    known = baseline + lifetime['known']
    history = {'known_estimated_cost_usd': amount(known), 'baseline_estimated_cost_usd': amount(baseline),
        'unknown_cost_jobs': lifetime['unknown'],
        'total_estimated_cost_usd': None if lifetime['unknown'] or lifetime['pending'] else amount(known)}
    daily = {'attempts': attempts, 'max_attempts': max_attempts, 'passes': len(passed), 'success_target': 20,
        'remaining_attempts': None if max_attempts is None else max_attempts - attempts,
        'remaining_passes': 20 - len(passed),
        'pending_records': current['pending'], 'duplicate_records': sum(not row['counts_slot'] for row in today),
        'known_estimated_cost_usd': amount(current['known']),
        'total_estimated_cost_usd': None if current['unknown'] or current['pending'] else amount(current['known']),
        'reserved_cost_usd': amount(current['reserved']), 'unknown_cost_jobs': current['unknown'],
        'inflight_cost_jobs': current['pending'], 'article_attempts': attempts - rag, 'rag_attempts': rag}
    matches = day == now.astimezone(KST).date()
    return day.isoformat(), daily if matches else None, history, today if matches else []


class Sources:
    def __init__(self, *, runner=None, state=STATE, binding=BINDING, apps=APPS):
        self.runner = runner or self._run
        self.state, self.binding, self.apps = Path(state), Path(binding), Path(apps)
        self.observed = {}

    @staticmethod
    def _run(args, data=None):
        result = subprocess.run(args, input=data, capture_output=True, timeout=12)
        require(result.returncode == 0 and len(result.stdout) <= MAX_BYTES)
        return result.stdout

    def read(self, path):
        path = Path(path)
        require(path.is_absolute() and path.resolve() == path and path.is_file() and not path.is_symlink()
            and all(not p.is_symlink() for p in path.parents) and path.stat().st_size <= MAX_BYTES)
        require(path == self.binding or path.is_relative_to(self.state) or path.is_relative_to(self.apps))
        raw = path.read_bytes()
        require(len(raw) <= MAX_BYTES)
        if path in self.observed and self.observed[path] != raw:
            raise SourceChanged('source_changed')
        self.observed[path] = raw
        return raw

    def child(self, relative):
        require(isinstance(relative, str) and not Path(relative).is_absolute() and '..' not in Path(relative).parts)
        path = self.state / relative
        require(path.resolve().is_relative_to(self.state.resolve()))
        return path

    def receipt(self, path):
        path = Path(path)
        if path.is_relative_to(Path('/runtime/state')):
            path = self.state / path.relative_to(Path('/runtime/state'))
        require(path.is_relative_to(self.state))
        return parse(self.read(path))

    def checkpoint(self):
        bindings = {}
        for line in self.read(self.binding).decode().splitlines():
            if line.startswith('DAILY_SCHEDULE_'):
                key, value = line.split('=', 1)
                tokens = shlex.split(value)
                require(key not in bindings and len(tokens) == 1)
                bindings[key] = tokens[0]
        require(bindings.get('DAILY_SCHEDULE_STATE_ROOT') == str(self.state))
        policy = parse(self.read(Path(bindings['DAILY_SCHEDULE_POLICY'])))
        require(digest(policy) == bindings.get('DAILY_SCHEDULE_SHA256'))
        schedule_attempt_limit(policy)
        journal = parse(self.read(self.state / 'scheduled/journal.json'))
        require(journal.get('policy_sha256') == digest(policy) and journal.get('schema_version') == 1)
        days = journal.get('days')
        require(isinstance(days, list) and 1 <= len(days) <= MAX_RECORDS)
        require(all(a.get('date', '') < b.get('date', '') for a, b in zip(days, days[1:])))
        entry = days[-1]
        checkpoint = parse(self.read(self.child(entry['checkpoint'])))
        require(checkpoint.get('daily_policy', {}).get('policy_date') == entry['date']
            and checkpoint.get('daily_plan_sha256') == entry.get('plan_sha256'))
        if entry.get('phase') == 'complete':
            require(hashlib.sha256(self.observed[self.child(entry['checkpoint'])]).hexdigest() == entry.get('checkpoint_sha256'))
        return checkpoint

    def unchanged(self):
        return all(path.is_file() and not path.is_symlink() and path.read_bytes() == raw for path, raw in self.observed.items())

    def jobs(self, records):
        ids = {'article': [], 'rag': []}
        for row in records:
            if row.get('counts_slot') and row.get('job_id'):
                require(str(UUID(row['job_id'])) == row['job_id'])
                ids['rag' if row.get('producer') == 'ani_rag' else 'article'].append(row['job_id'])
        # Input/resource safety bound, independent of the reviewed daily cap.
        require(sum(map(len, ids.values())) <= MAX_RECORDS)
        fragments = []
        for kind, table in (('article', 'content_generation_job'), ('rag', 'content_rag_generation_job')):
            where = 'job_id IN (' + ','.join("'%s'::uuid" % item for item in ids[kind]) + ')' if ids[kind] else 'false'
            if kind == 'article':
                where += " AND job_kind='anime_generate'"
            fragments.append("'%s',(SELECT coalesce(json_agg(t),'[]') FROM (SELECT job_id,execution_status,outcome FROM public.%s WHERE %s) t)" % (kind, table, where))
        sql = ("BEGIN READ ONLY; SET LOCAL statement_timeout=5000; SELECT json_build_object('database',current_database(),"
            "'version',(SELECT version FROM public.menu_worker_schema)," + ','.join(fragments) + '); ROLLBACK;')
        raw = self.runner([*DOCKER, 'exec', '-i', JOBS, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
            '-U', 'menu_jobs', '-d', 'menu_content_jobs'], sql.encode())
        rows = parse(raw)
        require(rows.get('database') == 'menu_content_jobs' and rows.get('version') == 8)
        result = {}
        for kind in ids:
            require(isinstance(rows.get(kind), list) and {r['job_id'] for r in rows[kind]} == set(ids[kind])
                and len(rows[kind]) == len(ids[kind]))
            counts = dict.fromkeys((*STATUSES, *OUTCOMES), 0)
            for row in rows[kind]:
                require(row['execution_status'] in STATUSES and row['outcome'] in (*OUTCOMES, None))
                counts[row['execution_status']] += 1
                if row['outcome'] is not None:
                    counts[row['outcome']] += 1
            result[kind] = counts
        return result

    def broker(self):
        calls = []
        for queue in ('anime-generation-live', 'content.rag_generate'):
            for priority in (0, 3, 6, 9):
                # Redis transport priority separator is two literal bytes.
                suffix = "..string.char(6,22)..'%s'" % priority if priority else ''
                calls.append("redis.call('LLEN','%s'%s)" % (queue, suffix))
        calls += ["redis.call('HLEN','unacked')", "redis.call('ZCARD','unacked_index')"]
        raw = self.runner([*DOCKER, 'exec', REDIS, 'redis-cli', '--raw', '-n', '0', 'EVAL_RO',
            'return {' + ','.join(calls) + '}', '0'])
        values = [int(value) for value in raw.decode().splitlines()]
        require(len(values) == 10 and all(0 <= v <= 1000000 for v in values))
        return {'article_queued': sum(values[:4]), 'rag_queued': sum(values[4:8]),
            'unacked': values[8], 'unacked_index': values[9]}

    def runtime(self):
        states = {}
        for key, unit in (('controller', 'me-nu-daily-schedule.service'), ('timer', 'me-nu-daily-schedule.timer')):
            value = self.runner(['systemctl', 'show', unit, '--property=ActiveState', '--value']).decode().strip()
            states[key] = value if value in {'active', 'activating', 'inactive', 'failed'} else 'unknown'
        ids = self.runner([*DOCKER, 'ps', '-aq', '--filter', 'label=com.docker.compose.project=menu-daily-generation']).decode().split()
        require(len(ids) <= 20 and all(re.fullmatch(r'[0-9a-f]{12,64}', value) for value in ids))
        rows = parse(self.runner([*DOCKER, 'inspect', *ids])) if ids else []
        for key, service in (('article_worker', 'generation-worker'), ('rag_worker', 'rag-worker')):
            matches = [row for row in rows if (row.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service') == service]
            states[key] = ('running' if matches[0]['State']['Running'] else 'stopped') if len(matches) == 1 else 'missing' if not matches else 'unknown'
        return states


def envelope(now, **fields):
    return {'schema_version': 1, 'purpose': 'generation_observability', 'generated_at': now.astimezone(timezone.utc).isoformat(),
        'timezone': 'Asia/Seoul', 'policy_date': None, 'availability': 'unavailable', 'reason': 'source_unavailable',
        'daily': None, 'history': None, 'jobs': None, 'broker': None, 'runtime': None, **fields}


def collect(*, factory=Sources, now=None, attempts=3):
    now = now or datetime.now(timezone.utc)
    require(now.tzinfo is not None and 1 <= attempts <= 3)
    for _ in range(attempts):
        try:
            source = factory()
            state = source.checkpoint()
            day, daily, history, records = account(state, now=now, load_receipt=source.receipt)
            jobs = source.jobs(records) if daily is not None else None
            broker, runtime = source.broker(), source.runtime()
            if not source.unchanged():
                continue
            partial = history['total_estimated_cost_usd'] is None or daily is None
            return envelope(now, policy_date=day, daily=daily, history=history, jobs=jobs, broker=broker, runtime=runtime,
                availability='partial' if partial else 'available',
                reason='policy_day_mismatch' if daily is None else 'source_partial' if partial else None)
        except SourceChanged:
            continue
        except (ObservationError, OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
            # A journal/checkpoint can be atomically replaced between reads, or
            # Docker/psql can briefly be unavailable. Retry the complete read
            # from a fresh Sources instance; never reuse a partial observation.
            continue
    return envelope(now)


def atomic_write(directory, snapshot):
    directory = Path(directory)
    require(directory.is_absolute() and directory.resolve() == directory and directory.is_dir()
        and not directory.is_symlink() and all(not p.is_symlink() for p in directory.parents))
    destination = directory / 'generation.json'
    require(not destination.is_symlink())
    raw = (json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()
    require(len(raw) <= 128 * 1024)
    fd, name = tempfile.mkstemp(prefix='.generation-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644) if hasattr(os, 'fchmod') else None
        os.replace(name, destination)
        if os.name != 'nt':
            parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(parent)
            finally: os.close(parent)
    finally:
        if os.path.exists(name): os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    snapshot = collect()
    atomic_write(args.output_directory, snapshot)
    print(json.dumps({'availability': snapshot['availability'], 'reason': snapshot['reason'],
        'generated_at': snapshot['generated_at'], 'database_writes': 0, 'queue_writes': 0, 'llm_calls': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'availability': 'unavailable', 'reason': 'snapshot_write_failed',
            'database_writes': 0, 'queue_writes': 0, 'llm_calls': 0}))
        raise SystemExit(1)
