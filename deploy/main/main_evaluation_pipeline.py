"""Main service-data collection, prospective evaluation, and atomic publication.

The host owns orchestration only. The deployed API image runs the collector as
UID 10001 on the internal Main network with a read-only DB transaction.
Raw case state stays outside the API mount; only aggregate status is published.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid

import main_publish_evaluation as publisher
import main_runtime as runtime

ROOT = publisher.ROOT
JOB_LOCK = Path('/run/lock/me-nu-main-evaluation-pipeline.lock')
# Retain the legacy state and lock after timer handover. Cases, the HMAC key,
# pending candidate and receipts must never be reset by a namespace change.
LEGACY_JOB_LOCK = Path('/run/lock/me-nu-checking-evaluation-pipeline.lock')
NETWORK = 'me-nu-checking_data'
SAFE_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
SOURCES = {'production', 'local_demo'}
IDENTIFIER = re.compile(r'^[a-zA-Z0-9_.:-]{1,100}$')


class PipelineError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def state_root():
    # This legacy-named directory is production evaluation state; keep it when
    # retiring Checking code, containers or units. No transfer is needed.
    return ROOT.parent / '.checking-evaluation-pipeline'


def status_path():
    return ROOT / 'deploy/main/admin-evaluation/pipeline-status.json'


@contextmanager
def lock(path):
    import fcntl
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineError('operation_in_progress') from None
        yield


def safe_summary(value):
    """The collector cannot expose user identities or arbitrary text in status."""
    if not isinstance(value, dict) or value.get('state') not in {'published', 'waiting', 'unchanged'}:
        raise PipelineError('invalid_collector_receipt')
    result = {'state': value['state']}
    reason = value.get('reason')
    if reason is not None and (not isinstance(reason, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', reason)):
        raise PipelineError('invalid_collector_receipt')
    result['reason'] = reason
    for key in ('collectedCases', 'matureCases', 'eligibleCases'):
        number = value.get(key, 0)
        if type(number) is not int or not 0 <= number <= 100_000_000:
            raise PipelineError('invalid_collector_receipt')
        result[key] = number
    counts = value.get('cohortSampleSizes', {})
    if not isinstance(counts, dict) or set(counts) - {'existing', 'new'}:
        raise PipelineError('invalid_collector_receipt')
    result['cohortSampleSizes'] = {}
    for cohort in ('existing', 'new'):
        number = counts.get(cohort, 0)
        if type(number) is not int or not 0 <= number <= 100_000:
            raise PipelineError('invalid_collector_receipt')
        result['cohortSampleSizes'][cohort] = number
    evaluation_id = value.get('lastEvaluationId')
    if evaluation_id is not None and (not isinstance(evaluation_id, str) or not IDENTIFIER.fullmatch(evaluation_id)):
        raise PipelineError('invalid_collector_receipt')
    result['lastEvaluationId'] = evaluation_id
    return result


def atomic_json(path, value, mode=0o600):
    publisher.no_symlinks(path)
    payload = (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    if len(payload) > 16_384:
        raise PipelineError('status_too_large')
    descriptor, temporary = tempfile.mkstemp(prefix='.pipeline-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        publisher.sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_status():
    path = status_path()
    if not path.exists():
        return {}
    publisher.no_symlinks(path)
    if path.stat().st_size > 16_384:
        raise PipelineError('status_too_large')
    result = json.loads(path.read_bytes())
    return result if isinstance(result, dict) else {}


def write_status(receipt, *, state, data_source, observation_days, min_samples, reason=None):
    prior = read_status()
    stamp = now()
    value = {
        'schemaVersion': 1, 'dataset': 'service', 'state': state, 'reason': reason,
        'dataSource': data_source, 'observationDays': observation_days, 'minSamples': min_samples,
        'collectedCases': 0, 'matureCases': 0, 'eligibleCases': 0,
        'cohortSampleSizes': {'existing': 0, 'new': 0}, 'lastEvaluationId': None,
        'lastAttemptAt': stamp, 'lastSuccessAt': prior.get('lastSuccessAt'),
        'lastPublishedAt': prior.get('lastPublishedAt'),
    }
    if receipt:
        value.update(safe_summary(receipt))
        value['state'] = state
        if reason is not None:
            value['reason'] = reason
    if state in {'waiting', 'unchanged', 'published'}:
        value['lastSuccessAt'] = stamp
    if state == 'published':
        value['lastPublishedAt'] = stamp
    atomic_json(status_path(), value, 0o644)
    return value


def record_preflight_failure(observation_days, min_samples):
    """Best effort only: report failure without trusting failed probes or errors.

    Do not create an artifact directory before deployment has prepared it. The
    last valid aggregate receipt may survive an unhealthy API or a rollout;
    retain those measurements and success times while marking this attempt.
    """
    try:
        path = status_path()
        publisher.no_symlinks(path)
        directory = path.parent.stat()
        if not stat.S_ISDIR(directory.st_mode) or stat.S_IMODE(directory.st_mode) != 0o755:
            return
        if path.exists() and not path.is_file():
            return
        prior = read_status()
        summary = safe_summary({**prior, 'state': 'waiting', 'reason': 'preflight_failed'})
        source = prior.get('dataSource')
        if not isinstance(source, str) or source not in SOURCES:
            source = None

        def previous_time(name):
            value = prior.get(name)
            if not isinstance(value, str):
                return None
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return value if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
            except (ValueError, OverflowError):
                return None

        value = {
            'schemaVersion': 1, 'dataset': 'service', **summary,
            'state': 'failed', 'reason': 'preflight_failed', 'dataSource': source,
            'observationDays': observation_days, 'minSamples': min_samples,
            'lastAttemptAt': now(), 'lastSuccessAt': previous_time('lastSuccessAt'),
            'lastPublishedAt': previous_time('lastPublishedAt'),
        }
        atomic_json(path, value, 0o644)
    except Exception:
        # In particular, unsafe/missing paths, corrupt old receipts or disk
        # failures must not mask the original preflight error or print it.
        pass


def api_configuration():
    # Values stay in memory; no environment dump, command argument, or log.
    probe = ("import json,os; print(json.dumps({'url':os.environ.get('DATABASE_URL'),"
             "'source':os.environ.get('EVENT_DATA_SOURCE','local_demo'),"
             "'deployment':os.environ.get('MENU_DEPLOYMENT_ENV')}))")
    raw = publisher.run([*publisher.DOCKER, 'exec', publisher.CONTAINER, 'python', '-c', probe])
    try:
        configuration = json.loads(raw)
        parsed = urlsplit(configuration['url'])
        if (configuration['deployment'] != 'production' or configuration['source'] not in SOURCES
                or parsed.scheme not in {'postgresql', 'postgresql+psycopg'}
                or parsed.hostname != 'postgres' or parsed.port not in {None, 5432}
                or parsed.path != '/me_nu_checking' or not parsed.username or not parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise PipelineError('main_configuration_invalid') from None
    if publisher.run([*publisher.DOCKER, 'network', 'inspect', '--format', '{{.Internal}}', NETWORK]) != 'true':
        raise PipelineError('main_network_not_internal')
    return configuration['url'], configuration['source']


def owned_directory(path):
    publisher.no_symlinks(path)
    created = not path.exists()
    path.mkdir(mode=0o700, exist_ok=True)
    if os.name != 'nt':
        if created:
            os.chown(path, 10001, 10001)
        info = path.stat()
        if info.st_uid != 10001 or info.st_gid != 10001 or stat.S_IMODE(info.st_mode) != 0o700:
            raise PipelineError('collector_state_permissions_invalid')


def collector_command(image, case_state, output, *, data_source, observation_days, min_samples, name):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise PipelineError('immutable_image_required')
    if data_source not in SOURCES:
        raise PipelineError('data_source_invalid')
    if not 1 <= observation_days <= 30 or not 1 <= min_samples <= 100_000:
        raise PipelineError('evaluation_configuration_invalid')
    sources = (ROOT / 'model/eval', case_state, output)
    for source in sources:
        publisher.no_symlinks(source)
        if ',' in str(source):
            raise PipelineError('unsafe_bind_path')
    return [*publisher.DOCKER, 'run', '--rm', '--name', name, '--network', NETWORK,
            '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '64',
            '--memory', '384m', '--memory-swap', '384m', '--cpus', '0.25',
            '--log-driver', 'none', '--env', 'DATABASE_URL', '--env', 'PYTHONPATH=/publisher',
            '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000',
            '--mount', f'type=bind,src={sources[0]},dst=/publisher/model/eval,readonly',
            '--mount', f'type=bind,src={case_state},dst=/state',
            '--mount', f'type=bind,src={output},dst=/output',
            '--entrypoint', 'python', image, '-m', 'model.eval.service_pipeline',
            '--state-dir', '/state', '--output', '/output/service-evaluation.json',
            '--expected-database', 'me_nu_checking', '--data-source', data_source,
            '--observation-days', str(observation_days), '--min-samples', str(min_samples)]


def collect(identity, database_url, case_state, output, *, data_source, observation_days, min_samples):
    name = 'main-evaluation-job-' + uuid.uuid4().hex
    command = collector_command(identity[1], case_state, output, data_source=data_source,
                                observation_days=observation_days, min_samples=min_samples, name=name)
    environment = {**SAFE_ENV, 'DATABASE_URL': database_url}
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=600)
        if result.returncode:
            raise PipelineError('collector_failed')
        if len(result.stdout) > 16_384:
            raise PipelineError('invalid_collector_receipt')
        try:
            receipt = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise PipelineError('invalid_collector_receipt') from None
        if not isinstance(receipt, dict):
            raise PipelineError('invalid_collector_receipt')
        if receipt.get('dataSource') != data_source or receipt.get('observationDays') != observation_days or receipt.get('minSamples') != min_samples:
            raise PipelineError('collector_configuration_mismatch')
        safe = safe_summary(receipt)
        if receipt.get('artifactWritten') is not (safe['state'] == 'published'):
            raise PipelineError('invalid_collector_receipt')
        return safe
    except subprocess.TimeoutExpired:
        raise PipelineError('collector_timeout') from None
    finally:
        # The generated name cannot target a service container. Docker --rm
        # handles normal completion; explicit cleanup also handles client death.
        try:
            subprocess.run([*publisher.DOCKER, 'rm', '-f', name], cwd=ROOT, env=SAFE_ENV,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise PipelineError('collector_cleanup_failed') from None


def publish_pending(identity, candidate):
    with lock(publisher.LOCK):
        if publisher.runtime_identity(status_path().parent, 'service') != identity:
            raise PipelineError('deployment_changed')
        return publisher.publish(candidate, 'service')


def run_once(observation_days=7, min_samples=20):
    try:
        runtime.require_legacy_stopped('evaluation')
        identity = publisher.runtime_identity(status_path().parent, 'service')
        database_url, data_source = api_configuration()
    except Exception:
        record_preflight_failure(observation_days, min_samples)
        raise
    private = state_root()
    publisher.private_directory(private)
    case_state, output = private / 'cases', private / 'pending'
    owned_directory(case_state)
    owned_directory(output)
    metadata = private / 'pending-receipt.json'
    candidate = output / 'service-evaluation.json'
    publisher.no_symlinks(candidate)
    receipt = None
    write_status(None, state='running', data_source=data_source,
                 observation_days=observation_days, min_samples=min_samples)
    try:
        if candidate.exists():
            # A completed candidate survives publication failure or a host
            # crash. Retry it before collecting, even if the collector's input
            # fingerprint is already marked unchanged.
            publisher.no_symlinks(metadata)
            saved = json.loads(publisher.read_regular(metadata))
            if saved.get('dataSource') != data_source or saved.get('observationDays') != observation_days or saved.get('minSamples') != min_samples:
                raise PipelineError('pending_configuration_mismatch')
            receipt = safe_summary(saved.get('receipt', {'state': 'published', 'reason': 'publication_retry'}))
        else:
            atomic_json(metadata, {'dataSource': data_source, 'observationDays': observation_days, 'minSamples': min_samples})
            receipt = collect(identity, database_url, case_state, output, data_source=data_source,
                              observation_days=observation_days, min_samples=min_samples)
            if publisher.runtime_identity(status_path().parent, 'service') != identity:
                raise PipelineError('deployment_changed')
            atomic_json(metadata, {'dataSource': data_source, 'observationDays': observation_days,
                                   'minSamples': min_samples, 'receipt': receipt})
        if receipt['state'] == 'published':
            if not candidate.is_file():
                raise PipelineError('candidate_missing')
            result = publish_pending(identity, candidate)
            final_state = 'published' if result['published'] else 'unchanged'
            candidate.unlink()
            metadata.unlink()
        else:
            if candidate.exists():
                raise PipelineError('unexpected_candidate')
            final_state = receipt['state']
            metadata.unlink()
        return write_status(receipt, state=final_state, data_source=data_source,
                            observation_days=observation_days, min_samples=min_samples)
    except BaseException as error:
        reason = error.code if isinstance(error, PipelineError) else 'pipeline_failed'
        write_status(receipt, state='failed', reason=reason, data_source=data_source,
                     observation_days=observation_days, min_samples=min_samples)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-days', type=int, default=7, choices=range(1, 31), metavar='1..30')
    parser.add_argument('--min-samples', type=int, default=20, choices=range(1, 100_001), metavar='1..100000')
    args = parser.parse_args(argv)
    try:
        runtime.require_host(__file__)
        def interrupted(*_):
            raise PipelineError('interrupted')
        signal.signal(signal.SIGTERM, interrupted)
        # Order: Main job, legacy job, then Main rollout (during publication).
        # The legacy lock also excludes manually launched old collectors.
        with lock(JOB_LOCK), lock(LEGACY_JOB_LOCK):
            result = run_once(args.observation_days, args.min_samples)
        print(json.dumps(result))
        return 0
    except (PipelineError, publisher.PublishError, runtime.RuntimeGuardError, OSError, ValueError, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, PipelineError) else 'pipeline_failed'
        print(json.dumps({'state': 'failed', 'reason': code}), file=sys.stderr)
        return 0 if code == 'operation_in_progress' else 1


if __name__ == '__main__':
    raise SystemExit(main())
