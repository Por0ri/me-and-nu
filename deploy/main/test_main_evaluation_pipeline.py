"""Host orchestration tests without Docker, PostgreSQL, timers, or real artifacts."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import main_evaluation as evaluation
import main_publish_evaluation as publisher
import main_evaluation_pipeline as pipeline

SHA = 'a' * 40
IMAGE = 'sha256:' + 'b' * 64
IDENTITY = (SHA, IMAGE)
SOURCE = 'local_demo'
API_CONFIGURATION = pipeline.api_configuration


def receipt(state='published'):
    return {'state': state, 'reason': 'candidate_ready' if state == 'published' else 'insufficient_samples',
            'collectedCases': 30, 'matureCases': 24, 'eligibleCases': 20,
            'cohortSampleSizes': {'existing': 20, 'new': 0}, 'lastEvaluationId': 'service-test'}


class EvaluationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve()
        self.root = self.base / 'checkout'
        self.artifacts = self.root / 'deploy/main/admin-evaluation'
        self.artifacts.mkdir(parents=True)
        self.offline = self.artifacts / 'evaluation.json'
        self.offline.write_bytes(b'offline unchanged')
        self.state = self.base / '.checking-evaluation-pipeline'
        self.candidate = self.state / 'pending/service-evaluation.json'
        self.patches = [patch.object(pipeline, 'ROOT', self.root), patch.object(publisher, 'ROOT', self.root),
                        patch.object(publisher, 'runtime_identity', return_value=IDENTITY),
                        patch.object(pipeline, 'api_configuration', return_value=('PRIVATE_DATABASE_URL', SOURCE)),
                        patch.object(pipeline.runtime, 'require_legacy_stopped'),
                        patch.object(pipeline, 'lock', side_effect=lambda _: nullcontext())]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def collector(self, state='published'):
        def collect(*args, **kwargs):
            if state == 'published':
                self.candidate.write_bytes(b'validated candidate')
            return receipt(state)
        return collect

    def test_success_collects_publishes_and_only_exposes_aggregate_status(self):
        with patch.object(pipeline, 'collect', side_effect=self.collector()) as collect, \
                patch.object(publisher, 'publish', return_value={'published': True}) as publish:
            result = pipeline.run_once()
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['dataSource'], SOURCE)
        self.assertEqual(publish.call_args.args, (self.candidate, 'service'))
        self.assertFalse(self.candidate.exists())
        self.assertEqual(self.offline.read_bytes(), b'offline unchanged')
        self.assertEqual(collect.call_args.kwargs['data_source'], SOURCE)
        status = (self.artifacts / 'pipeline-status.json').read_text()
        self.assertNotIn('PRIVATE_DATABASE_URL', status)
        self.assertNotIn(str(self.root), status)
        self.assertIsNotNone(result['lastSuccessAt'])
        self.assertIsNotNone(result['lastPublishedAt'])

    def test_retained_case_state_and_hmac_are_preserved(self):
        cases = self.state / 'cases'
        cases.mkdir(parents=True)
        secret = cases / 'hmac.key'
        secret.write_bytes(b'PRIVATE_EXISTING_HMAC')
        with patch.object(pipeline, 'collect', side_effect=self.collector('waiting')):
            pipeline.run_once()
        self.assertEqual(pipeline.state_root(), self.state)
        self.assertEqual(secret.read_bytes(), b'PRIVATE_EXISTING_HMAC')
        self.assertFalse((self.base / '.main-evaluation-pipeline').exists())

    def test_active_legacy_evaluation_blocks_collection_before_state_creation(self):
        with patch.object(pipeline.runtime, 'require_legacy_stopped',
                          side_effect=pipeline.runtime.RuntimeGuardError('legacy_evaluation_active')), \
                patch.object(pipeline, 'collect') as collect:
            with self.assertRaises(pipeline.runtime.RuntimeGuardError):
                pipeline.run_once()
        collect.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_api_configuration_requires_production_and_retains_shared_database(self):
        valid = {'url': 'postgresql+psycopg://me_nu_checking:PRIVATE@postgres:5432/me_nu_checking',
                 'source': SOURCE, 'deployment': 'production'}
        with patch.object(publisher, 'run', side_effect=[json.dumps(valid), 'true']):
            self.assertEqual(API_CONFIGURATION(), (valid['url'], SOURCE))
        for changed in ({'deployment': 'checking'}, {'source': 'automated_test'},
                        {'url': valid['url'].replace('/me_nu_checking', '/other_database')},
                        {'url': valid['url'].replace('@postgres:', '@external:')}):
            with self.subTest(changed=changed), patch.object(publisher, 'run', return_value=json.dumps(valid | changed)):
                with self.assertRaisesRegex(pipeline.PipelineError, '^main_configuration_invalid$') as raised:
                    API_CONFIGURATION()
                self.assertNotIn('PRIVATE', str(raised.exception))

    def test_entrypoint_holds_main_then_legacy_job_locks(self):
        acquired = []
        with patch.object(pipeline.runtime, 'require_host'), \
                patch.object(pipeline, 'lock', side_effect=lambda path: acquired.append(path) or nullcontext()), \
                patch.object(pipeline, 'run_once', return_value={'state': 'waiting'}), \
                patch('builtins.print'):
            self.assertEqual(pipeline.main([]), 0)
        self.assertEqual(acquired, [pipeline.JOB_LOCK, pipeline.LEGACY_JOB_LOCK])

    def test_timer_enable_checks_legacy_before_installing_any_unit(self):
        with patch.object(evaluation.runtime, 'require_host'), \
                patch.object(evaluation.os, 'geteuid', return_value=0, create=True), \
                patch.object(evaluation.runtime, 'require_legacy_stopped',
                             side_effect=pipeline.runtime.RuntimeGuardError('legacy_active')), \
                patch.object(evaluation, 'install_units') as install, \
                patch.object(publisher, 'run') as run, patch('builtins.print'):
            self.assertEqual(evaluation.main(['enable']), 1)
        install.assert_not_called()
        run.assert_not_called()

    def test_waiting_and_unchanged_do_not_publish(self):
        for state in ('waiting', 'unchanged'):
            with self.subTest(state=state), patch.object(pipeline, 'collect', side_effect=self.collector(state)), \
                    patch.object(publisher, 'publish') as publish:
                result = pipeline.run_once()
            publish.assert_not_called()
            self.assertEqual(result['state'], state)
            self.assertEqual(self.offline.read_bytes(), b'offline unchanged')

    def test_publish_failure_preserves_pending_then_retry_skips_collector(self):
        with patch.object(pipeline, 'collect', side_effect=self.collector()), \
                patch.object(publisher, 'publish', side_effect=publisher.PublishError('synthetic failure')):
            with self.assertRaises(publisher.PublishError):
                pipeline.run_once()
        self.assertTrue(self.candidate.exists())
        self.assertEqual(json.loads((self.artifacts / 'pipeline-status.json').read_text())['state'], 'failed')
        with patch.object(pipeline, 'collect') as collect, \
                patch.object(publisher, 'publish', return_value={'published': True}):
            result = pipeline.run_once()
        collect.assert_not_called()
        self.assertFalse(self.candidate.exists())
        self.assertEqual(result['state'], 'published')

    def test_crash_after_candidate_write_before_receipt_recovers(self):
        def interrupted(*args, **kwargs):
            self.candidate.write_bytes(b'completed candidate')
            raise pipeline.PipelineError('interrupted')
        with patch.object(pipeline, 'collect', side_effect=interrupted):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.run_once()
        with patch.object(pipeline, 'collect') as collect, \
                patch.object(publisher, 'publish', return_value={'published': True}):
            result = pipeline.run_once()
        collect.assert_not_called()
        self.assertEqual(result['reason'], 'publication_retry')

    def test_pending_source_cannot_be_relabelled(self):
        with patch.object(pipeline, 'collect', side_effect=self.collector()), \
                patch.object(publisher, 'publish', side_effect=publisher.PublishError('fail')):
            with self.assertRaises(publisher.PublishError):
                pipeline.run_once()
        with patch.object(pipeline, 'api_configuration', return_value=('hidden', 'production')), \
                patch.object(publisher, 'publish') as publish:
            with self.assertRaisesRegex(pipeline.PipelineError, 'pending_configuration_mismatch'):
                pipeline.run_once()
        publish.assert_not_called()
        self.assertTrue(self.candidate.exists())

    def test_deploy_change_retains_candidate_without_publication(self):
        with patch.object(pipeline, 'collect', side_effect=self.collector()), \
                patch.object(publisher, 'runtime_identity', side_effect=[IDENTITY, ('c' * 40, IMAGE)]), \
                patch.object(publisher, 'publish') as publish:
            with self.assertRaisesRegex(pipeline.PipelineError, 'deployment_changed'):
                pipeline.run_once()
        publish.assert_not_called()
        self.assertTrue(self.candidate.exists())

    def test_preflight_failure_keeps_aggregates_and_success_times_without_private_output(self):
        previous = {**receipt(), 'dataSource': SOURCE,
                    'lastSuccessAt': '2026-11-22T01:00:00+00:00',
                    'lastPublishedAt': '2026-11-21T01:00:00+00:00',
                    'databaseUrl': 'PRIVATE_DATABASE_URL', 'userId': 'PRIVATE_USER'}
        status = self.artifacts / 'pipeline-status.json'
        # Windows directory chmod does not implement Unix permission bits;
        # simulate only the mode check. The real path, reads and atomic write
        # remain in use, and Linux enforces 0755 in the entrypoint itself.
        for probe in ('runtime_identity', 'api_configuration'):
            with self.subTest(probe=probe):
                status.write_text(json.dumps(previous), encoding='utf-8')
                owner = publisher if probe == 'runtime_identity' else pipeline
                original = publisher.PublishError('PRIVATE_DATABASE_URL')
                with patch.object(owner, probe, side_effect=original), \
                        patch.object(pipeline.stat, 'S_IMODE', return_value=0o755), \
                        patch.object(pipeline, 'collect') as collect:
                    with self.assertRaises(publisher.PublishError) as raised:
                        pipeline.run_once()
                self.assertIs(raised.exception, original)
                collect.assert_not_called()
                updated = json.loads(status.read_text())
                self.assertEqual((updated['state'], updated['reason']), ('failed', 'preflight_failed'))
                for field in ('collectedCases', 'matureCases', 'eligibleCases', 'cohortSampleSizes',
                              'lastEvaluationId', 'lastSuccessAt', 'lastPublishedAt', 'dataSource'):
                    self.assertEqual(updated[field], previous[field])
                self.assertIn('lastAttemptAt', updated)
                self.assertNotIn('PRIVATE', status.read_text())
                self.assertEqual(self.offline.read_bytes(), b'offline unchanged')

    def test_preflight_failure_never_invents_source_and_drops_invalid_timestamp(self):
        status = self.artifacts / 'pipeline-status.json'
        status.write_text(json.dumps({**receipt(), 'dataSource': 'PRIVATE_SOURCE',
                                     'lastSuccessAt': 'PRIVATE_TIMESTAMP', 'lastPublishedAt': '2026-11-22T00:00:00'}))
        with patch.object(publisher, 'runtime_identity', side_effect=publisher.PublishError('unhealthy')), \
                patch.object(pipeline.stat, 'S_IMODE', return_value=0o755):
            with self.assertRaises(publisher.PublishError):
                pipeline.run_once()
        updated = json.loads(status.read_text())
        self.assertIsNone(updated['dataSource'])
        self.assertIsNone(updated['lastSuccessAt'])
        self.assertIsNone(updated['lastPublishedAt'])
        self.assertNotIn('PRIVATE', status.read_text())

    def test_preflight_failure_does_not_write_missing_or_unsafe_artifact_directory(self):
        with patch.object(publisher, 'runtime_identity', side_effect=publisher.PublishError('unhealthy')), \
                patch.object(pipeline, 'atomic_json') as write:
            with patch.object(pipeline, 'status_path', return_value=self.base / 'absent/status.json'):
                with self.assertRaises(publisher.PublishError):
                    pipeline.run_once()
            self.assertFalse((self.base / 'absent').exists())
            with patch.object(pipeline.stat, 'S_IMODE', return_value=0o700):
                with self.assertRaises(publisher.PublishError):
                    pipeline.run_once()
            with patch.object(publisher, 'no_symlinks', side_effect=publisher.PublishError('unsafe path')):
                with self.assertRaises(publisher.PublishError):
                    pipeline.run_once()
        write.assert_not_called()

    def test_failed_status_write_preserves_original_preflight_error(self):
        original = pipeline.PipelineError('main_network_not_internal')
        with patch.object(pipeline, 'api_configuration', side_effect=original), \
                patch.object(pipeline.stat, 'S_IMODE', return_value=0o755), \
                patch.object(pipeline, 'atomic_json', side_effect=OSError('PRIVATE_DISK_ERROR')):
            with self.assertRaises(pipeline.PipelineError) as raised:
                pipeline.run_once()
        self.assertIs(raised.exception, original)

    def test_safe_summary_discards_identity_and_raw_input(self):
        raw = {**receipt(), 'userId': 'PRIVATE', 'databaseUrl': 'PRIVATE', 'ranks': [1, 2]}
        result = pipeline.safe_summary(raw)
        self.assertNotIn('PRIVATE', json.dumps(result))
        for bad in ({'reason': 'postgres://secret'}, {'collectedCases': True},
                    {'lastEvaluationId': '/private/file'}, {'cohortSampleSizes': {'account': 1}}):
            with self.subTest(bad=bad), self.assertRaises(pipeline.PipelineError):
                pipeline.safe_summary({**raw, **bad})

    def test_collector_command_has_only_internal_network_and_two_private_write_mounts(self):
        command = pipeline.collector_command(IMAGE, self.base / 'cases', self.base / 'output',
                                             data_source=SOURCE, observation_days=7, min_samples=20, name='isolated-test')
        self.assertEqual(command[:4], ['docker', '--context', 'default', 'run'])
        self.assertEqual(command[command.index('--network') + 1], 'me-nu-checking_data')
        self.assertEqual(command[command.index('--user') + 1], '10001:10001')
        self.assertIn('DATABASE_URL', command)
        self.assertNotIn('PRIVATE_DATABASE_URL', ' '.join(command))
        self.assertIn('PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000', command)
        mounts = [item for item in command if item.startswith('type=bind,')]
        self.assertEqual(len(mounts), 3)
        self.assertTrue(mounts[0].endswith(',readonly'))
        self.assertFalse(any(str(self.artifacts) in item or 'docker.sock' in item for item in mounts))

    def test_collect_failure_redacts_output_and_removes_only_its_job(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, b'private input', b'private credentials')
        with patch.object(subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(pipeline.PipelineError, '^collector_failed$'):
                pipeline.collect(IDENTITY, 'PRIVATE_DATABASE_URL', self.base / 'cases', self.base / 'out',
                                 data_source=SOURCE, observation_days=7, min_samples=20)
        self.assertEqual(calls[0][1]['env']['DATABASE_URL'], 'PRIVATE_DATABASE_URL')
        self.assertNotIn('PRIVATE_DATABASE_URL', ' '.join(calls[0][0]))
        self.assertEqual(calls[1][0][3:5], ['rm', '-f'])
        self.assertTrue(calls[1][0][-1].startswith('main-evaluation-job-'))

    def test_collector_rejects_non_service_sources(self):
        for source in ('automated_test', 'imported'):
            with self.subTest(source=source), self.assertRaisesRegex(pipeline.PipelineError, 'data_source_invalid'):
                pipeline.collector_command(IMAGE, self.base / 'cases', self.base / 'output',
                                           data_source=source, observation_days=7, min_samples=20, name='test')

    def test_malformed_receipt_and_cleanup_failure_are_controlled_errors(self):
        for output in (b'[]', b'null'):
            with self.subTest(output=output), patch.object(subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, output, b'')):
                with self.assertRaisesRegex(pipeline.PipelineError, 'invalid_collector_receipt'):
                    pipeline.collect(IDENTITY, 'PRIVATE_DATABASE_URL', self.base / 'cases', self.base / 'out',
                                     data_source=SOURCE, observation_days=7, min_samples=20)
        with patch.object(subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 1, b'private', b'private'),
                subprocess.TimeoutExpired('private-command', 30)]):
            with self.assertRaisesRegex(pipeline.PipelineError, '^collector_cleanup_failed$'):
                pipeline.collect(IDENTITY, 'PRIVATE_DATABASE_URL', self.base / 'cases', self.base / 'out',
                                 data_source=SOURCE, observation_days=7, min_samples=20)

    def test_timer_and_installer_do_not_start_arbitrary_services(self):
        root = Path(__file__).resolve().parents[2]
        service = (root / 'deploy/main/systemd/me-nu-main-evaluation.service').read_text()
        timer = (root / 'deploy/main/systemd/me-nu-main-evaluation.timer').read_text()
        self.assertIn('--observation-days 7 --min-samples 20', service)
        self.assertIn('OnCalendar=hourly', timer)
        self.assertIn('Persistent=true', timer)
        units = self.base / 'systemd'
        units.mkdir()
        with patch.object(evaluation, 'UNIT_DIRECTORY', units):
            with self.assertRaises(publisher.PublishError):
                evaluation.install_units({'other.service': b'bad'})
            evaluation.install_units({evaluation.SERVICE: service.encode(), evaluation.TIMER: timer.encode()})
        self.assertEqual({path.name for path in units.iterdir()}, set(evaluation.UNITS))

    def test_units_render_only_the_actual_reviewed_checkout_path(self):
        sources = Path(__file__).resolve().parent / 'systemd'
        directory = self.root / 'deploy/main/systemd'
        directory.mkdir()
        for name in evaluation.UNITS:
            (directory / name).write_bytes((sources / name).read_bytes())
        with patch.object(evaluation, 'ROOT', self.root):
            rendered = evaluation.units()
        service = rendered[evaluation.SERVICE].decode()
        self.assertIn(f'WorkingDirectory={self.root}', service)
        self.assertIn(f'{self.root}/deploy/main/main_evaluation_pipeline.py', service)
        self.assertNotIn(evaluation.UNIT_TEMPLATE_ROOT, service)
        self.assertNotIn('deploy/checking', service)


if __name__ == '__main__':
    unittest.main()
