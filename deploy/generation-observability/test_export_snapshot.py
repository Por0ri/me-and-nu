import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location('generation_export', Path(__file__).with_name('export_snapshot.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
NOW = datetime(2026, 11, 27, 3, tzinfo=timezone.utc)


def record(*, rag=False, status='succeeded', outcome='ready_for_staging', complete=True, cost='0.05'):
    key = uuid4().hex
    row = {'job_id': str(uuid4()), 'generation_attempt_id': str(uuid4()),
        'request': {'source_id': 'registered_source'},
        'canonical_url': 'urn:menu-rag:' + key * 2 if rag else 'https://official.example/' + key,
        'state': 'complete' if complete else 'reserved', 'counts_slot': True,
        'execution_status': status, 'outcome': outcome}
    if rag: row['producer'] = 'ani_rag'
    if cost is not None: row['estimated_cost_usd'] = cost
    return row


def state(rows=(), prefix=(), receipts=None):
    receipts = receipts or {}
    prefix_cost = m.costs(prefix, receipts.__getitem__)
    policy = {'schema_version': 3, 'policy_date': '2026-11-27', 'limit_mode': 'daily_counts',
        'max_attempts': 40, 'success_target': 20, 'deadline': '2026-11-28T00:00:00+09:00',
        'start_record_index': len(prefix), 'prior_records_sha256': m.digest(list(prefix)),
        'prior_slots': sum(r['counts_slot'] for r in prefix),
        'prior_accounted_usd': str(m.Decimal('0.01') + prefix_cost['known'] + prefix_cost['reserved']),
        'prior_unknown_costs': [{'job_id': r['job_id'], **r['failure_recovery_receipt']}
            for r in prefix if r.get('accounting_method') == 'unknown']}
    today = [dict(row, policy_date=policy['policy_date'], day_policy_sha256=m.digest(policy)) for row in rows]
    return {'config': {'limit_mode': 'daily_counts', 'total_budget_usd': None,
            'baseline_estimated_cost_usd': '0.01', 'deadline': policy['deadline']},
        'daily_policy': policy, 'daily_policies': [policy], 'records': [*copy.deepcopy(prefix), *today]}


def recovered(partial='0.02'):
    row = record(status='needs_attention', outcome=None, cost=None)
    row.update(accounting_method='unknown', cost_unknown=True)
    receipt = {'unknown_total': True, 'job': {'job_id': row['job_id'],
        'generation_attempt_id': row['generation_attempt_id']},
        'original_record': {'counts_slot': True}, 'known_partial_estimated_usd': partial}
    path = '/runtime/state/recovery/' + row['job_id'] + '.json'
    row['failure_recovery_receipt'] = {'receipt_path': path, 'receipt_sha256': m.digest(receipt)}
    return row, {path: receipt}


class FakeSources:
    def __init__(self, value, unchanged=True):
        self.value, self.stable = value, unchanged
    def checkpoint(self): return self.value
    def receipt(self, path): raise AssertionError('unexpected receipt')
    def jobs(self, rows):
        return {k: dict.fromkeys((*m.STATUSES, *m.OUTCOMES), 0) for k in ('article', 'rag')}
    def broker(self): return dict(article_queued=0, rag_queued=0, unacked=0, unacked_index=0)
    def runtime(self): return dict(controller='active', timer='active', article_worker='running', rag_worker='running')
    def unchanged(self): return self.stable


class AccountingTests(unittest.TestCase):
    def test_shared_counts_ignore_retry_deliveries_and_duplicate_requests(self):
        article, rag = record(), record(rag=True)
        article.update(attempt_count=3, dispatch_count=9)
        duplicate = {'counts_slot': False, 'state': 'duplicate'}
        value = state([article, rag, duplicate])
        day, daily, history, _ = m.account(value, now=NOW, load_receipt={ }.__getitem__)
        self.assertEqual((day, daily['attempts'], daily['passes']), ('2026-11-27', 2, 2))
        self.assertEqual((daily['article_attempts'], daily['rag_attempts'], daily['duplicate_records']), (1, 1, 1))
        self.assertEqual(daily['known_estimated_cost_usd'], '0.10')
        self.assertEqual(history['known_estimated_cost_usd'], '0.11')

    def test_pass_requires_complete_and_deduplicates_article_identity(self):
        first = record()
        second = record(); second['canonical_url'] = first['canonical_url']
        pending = record(rag=True, complete=False)
        _, daily, _, _ = m.account(state([first, second, pending]), now=NOW, load_receipt={ }.__getitem__)
        self.assertEqual((daily['attempts'], daily['passes'], daily['pending_records']), (3, 1, 1))
        self.assertIsNone(daily['total_estimated_cost_usd'])

    def test_historical_unknown_is_preserved_without_poisoning_today_total(self):
        old, receipts = recovered()
        _, daily, history, _ = m.account(state([record()], [old], receipts), now=NOW, load_receipt=receipts.__getitem__)
        self.assertEqual(daily['unknown_cost_jobs'], 0)
        self.assertEqual(daily['total_estimated_cost_usd'], '0.05')
        self.assertEqual(history['known_estimated_cost_usd'], '0.08')
        self.assertEqual(history['unknown_cost_jobs'], 1)
        self.assertIsNone(history['total_estimated_cost_usd'])

    def test_rag_partial_and_pending_are_not_zero_final_charges(self):
        partial = record(rag=True, status='failed', cost=None)
        partial.update(rag_cost_incomplete=True, rag_known_partial_usd='0.003')
        pending = record(rag=True, status='running', outcome=None, complete=False, cost=None)
        _, daily, _, _ = m.account(state([partial, pending]), now=NOW, load_receipt={ }.__getitem__)
        self.assertEqual(daily['known_estimated_cost_usd'], '0.003')
        self.assertEqual((daily['unknown_cost_jobs'], daily['inflight_cost_jobs']), (1, 1))
        self.assertIsNone(daily['total_estimated_cost_usd'])

    def test_receipt_tamper_and_cost_overlap_fail_closed(self):
        old, receipts = recovered()
        value = state([], [old], receipts)
        next(iter(receipts.values()))['known_partial_estimated_usd'] = '0.20'
        with self.assertRaises(m.ObservationError): m.account(value, now=NOW, load_receipt=receipts.__getitem__)
        row = record(rag=True); row.update(rag_cost_incomplete=True, rag_known_partial_usd='0.1')
        with self.assertRaises(m.ObservationError): m.account(state([row]), now=NOW, load_receipt={ }.__getitem__)

    def test_kst_midnight_never_labels_previous_day_counts_as_today(self):
        value = state([record()])
        snapshot = m.collect(factory=lambda: FakeSources(value), now=datetime(2026, 11, 27, 15, tzinfo=timezone.utc))
        self.assertEqual(snapshot['reason'], 'policy_day_mismatch')
        self.assertEqual(snapshot['policy_date'], '2026-11-27')
        self.assertIsNone(snapshot['daily']); self.assertIsNone(snapshot['jobs'])
        self.assertEqual(snapshot['history']['known_estimated_cost_usd'], '0.06')

    def test_hard_caps_deadline_and_prefix_binding(self):
        for value in (state([record() for _ in range(41)]), state([record() for _ in range(21)])):
            with self.assertRaises(m.ObservationError): m.account(value, now=NOW, load_receipt={ }.__getitem__)
        value = state([record()]); value['config']['deadline'] = '2026-11-28T01:00:00+09:00'
        with self.assertRaises(m.ObservationError): m.account(value, now=NOW, load_receipt={ }.__getitem__)

    def test_snapshot_race_retries_without_controller_locks_or_mutations(self):
        value = state([record()]); inputs = iter([False, False, True])
        result = m.collect(factory=lambda: FakeSources(value, next(inputs)), now=NOW)
        self.assertEqual(result['availability'], 'available')
        result = m.collect(factory=lambda: FakeSources(value, False), now=NOW)
        self.assertEqual(result['availability'], 'unavailable')
        self.assertIsNone(result['daily'])

    def test_atomic_output_contains_only_snapshot_not_credentials(self):
        value = state([record()]); value['private'] = 'DO_NOT_EXPORT'
        snapshot = m.collect(factory=lambda: FakeSources(value), now=NOW)
        with tempfile.TemporaryDirectory() as root:
            m.atomic_write(Path(root), snapshot)
            raw = (Path(root) / 'generation.json').read_text()
            self.assertNotIn('DO_NOT_EXPORT', raw)
            self.assertNotIn('canonical_url', raw)
            self.assertEqual(json.loads(raw), snapshot)
            self.assertEqual(len(list(Path(root).iterdir())), 1)

    def test_sql_scopes_owned_jobs_and_never_counts_attempt_rows(self):
        row = record(rag=True); calls = []
        def runner(args, data=None):
            calls.append((args, data))
            return json.dumps({'database': 'menu_content_jobs', 'version': 8, 'article': [],
                'rag': [{'job_id': row['job_id'], 'execution_status': 'succeeded', 'outcome': 'ready_for_staging'}]}).encode()
        result = m.Sources(runner=runner).jobs([row])
        sql = calls[0][1].decode()
        self.assertIn('BEGIN READ ONLY', sql); self.assertIn('ROLLBACK', sql)
        self.assertNotIn('content_generation_attempt', sql)
        self.assertEqual((result['rag']['succeeded'], result['rag']['ready_for_staging']), (1, 1))

    def test_broker_priorities_are_summed_but_unacked_not_double_counted(self):
        calls = []
        def runner(args, data=None):
            calls.append(args); return b'1\n2\n3\n4\n5\n6\n7\n8\n9\n9\n'
        result = m.Sources(runner=runner).broker()
        self.assertEqual(result, dict(article_queued=10, rag_queued=26, unacked=9, unacked_index=9))
        self.assertIn('EVAL_RO', calls[0]); self.assertIn('string.char(6,22)', calls[0][-2])

    def test_source_failure_replaces_all_metrics_with_unavailable(self):
        class Broken(FakeSources):
            def broker(self): raise OSError('DO_NOT_LEAK')
        result = m.collect(factory=lambda: Broken(state()), now=NOW)
        self.assertEqual(result['availability'], 'unavailable')
        self.assertTrue(all(result[k] is None for k in ('daily', 'history', 'jobs', 'broker', 'runtime')))
        self.assertNotIn('DO_NOT_LEAK', json.dumps(result))

    def test_actual_binding_checkpoint_read_and_atomic_replacement_race(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state_root = root / 'state'; apps = root / 'apps'
            (state_root / 'scheduled').mkdir(parents=True); apps.mkdir()
            policy = {'max_attempts': 40, 'success_target': 20}
            policy_path = apps / 'policy.json'; policy_path.write_text(json.dumps(policy))
            binding = root / 'schedule.env'
            binding.write_text('DAILY_SCHEDULE_STATE_ROOT=' + shlex.quote(str(state_root)) + '\n'
                + 'DAILY_SCHEDULE_POLICY=' + shlex.quote(str(policy_path)) + '\n'
                + 'DAILY_SCHEDULE_SHA256=' + m.digest(policy) + '\n')
            value = state([record()]); value['daily_plan_sha256'] = 'a' * 64
            checkpoint = state_root / 'daily-2026-11-27.json'; checkpoint.write_text(json.dumps(value))
            journal = {'schema_version': 1, 'policy_sha256': m.digest(policy), 'days': [
                {'date': '2026-11-27', 'phase': 'generating', 'checkpoint': checkpoint.name, 'plan_sha256': 'a' * 64}]}
            (state_root / 'scheduled/journal.json').write_text(json.dumps(journal))
            source = m.Sources(state=state_root, binding=binding, apps=apps)
            self.assertEqual(source.checkpoint(), value); self.assertTrue(source.unchanged())
            checkpoint.write_text(json.dumps({**value, 'updated_at': 2}))
            self.assertFalse(source.unchanged())
            with self.assertRaises(m.SourceChanged): source.read(checkpoint)

    def test_reservation_amount_is_not_a_paid_charge(self):
        row = record(cost=None, outcome='skipped')
        receipt = {'job_id': row['job_id'], 'reserved_usd': '1.00', 'accounting_method': 'reserve'}
        row.update(accounting_method='reserve', reserved_cost_usd='1.00',
            accounting_reservation={**receipt, 'receipt_sha256': m.digest({'schema_version': 1, 'records': [receipt]})})
        _, daily, history, _ = m.account(state([row]), now=NOW, load_receipt={ }.__getitem__)
        self.assertEqual(daily['reserved_cost_usd'], '1.00')
        self.assertEqual(daily['known_estimated_cost_usd'], '0')
        self.assertEqual(daily['unknown_cost_jobs'], 1)
        self.assertIsNone(daily['total_estimated_cost_usd']); self.assertIsNone(history['total_estimated_cost_usd'])

    def test_inspect_env_is_never_exported_and_missing_workers_not_healthy(self):
        def runner(args, data=None):
            if args[0] == 'systemctl': return b'active\n'
            if 'ps' in args: return b'abcdef123456\n'
            return json.dumps([{'Config': {'Labels': {'com.docker.compose.service': 'rag-worker'},
                'Env': ['LLM_KEY=DO_NOT_EXPORT']}, 'State': {'Running': True}}]).encode()
        result = m.Sources(runner=runner).runtime()
        self.assertEqual(result['article_worker'], 'missing')
        self.assertEqual(result['rag_worker'], 'running')
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))

    def test_worker_decimal_exponent_zero_is_emitted_as_fixed_point(self):
        _, daily, _, _ = m.account(state([record(cost='0E-8'), record(cost='1E-8')]),
            now=NOW, load_receipt={ }.__getitem__)
        self.assertEqual(daily['known_estimated_cost_usd'], '0.00000001')
        self.assertEqual(daily['total_estimated_cost_usd'], '0.00000001')
        for value in ('NaN', 'Infinity', '-0.01', '1e1000', '1e-1000'):
            with self.assertRaises(m.ObservationError): m.money(value)

    def test_transient_source_error_retries_the_entire_observation(self):
        class Transient(FakeSources):
            def checkpoint(self): raise OSError('atomic replacement in progress')
        sources = iter([Transient(state()), FakeSources(state())])
        result = m.collect(factory=lambda: next(sources), now=NOW)
        self.assertEqual(result['availability'], 'available')
        self.assertIsNotNone(result['daily'])


if __name__ == '__main__': unittest.main()
