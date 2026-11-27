"""Offline quota migration, CAS/replay and exact evidence preservation."""
import copy
import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch

import test_scheduled_run as fixtures

s = fixtures.scheduler
sys.modules['scheduled_run'] = s
m = importlib.import_module('success_only_migration')


class SuccessOnlyMigrationTests(unittest.TestCase):
    def setUp(self):
        f = self.f = fixtures.MixedScheduleTests('runTest')
        f.setUp(); self.addCleanup(f.doCleanups)
        # Match the operational shape: 23 historic records, today's 9 attempts
        # and 6 passes. Their original content and cost evidence never changes.
        for index in range(22):
            f.seed['records'].append({'state':'complete','counts_slot':True,
                'job_id':'historical-'+str(index),'estimated_cost_usd':'0.01'})
            f.seed['seen'].append('historic-'+str(index))
        s.save_json(f.state/'seed.json',f.seed)
        f.policy['seed_sha256']=s.file_sha(f.state/'seed.json')
        f.mixed(enabled=True,feed_valid_days=None)
        self.old=f.supervisor(); self.old.run(preflight=True)
        journal=self.old.load_journal(); entry=journal['days'][-1]
        plan=s.read_json(self.old.host(entry['directory']+'/plan.json'))
        policy=plan['daily_policy']; policy['start_record_index']=23
        plan['day_policy_sha256']=s.digest(policy); entry['plan_sha256']=s.digest(plan)
        child=copy.deepcopy(f.seed)
        child.update(config=plan['config'],daily_policy=policy,daily_policies=[policy],
            daily_plan_sha256=entry['plan_sha256'],status='stopped',stop_reason='failure_reconciled_paused',
            rag_scope=plan['rag_scope'],rag_scope_sha256=plan['rag_scope_sha256'])
        for number in range(9):
            url='https://example.invalid/retained/'+str(number)
            child['records'].append({'state':'complete','counts_slot':True,'job_id':'today-'+str(number),
                'request':f.routes[0],'canonical_url':url,'policy_date':entry['date'],
                'day_policy_sha256':s.digest(policy),'execution_status':'succeeded' if number<6 else 'failed',
                'outcome':'ready_for_staging' if number<6 else None,'estimated_cost_usd':'0.001'})
            child['seen'].append(url)
        receipt={'purpose':'retained_A08_zero_preflight_recovery','original_record':copy.deepcopy(child['records'][-1])}
        s.save_json(f.state/'failure-receipt.json',receipt)
        entry.update(phase='reconciled_paused',failure_recoveries=[{'receipt':'failure-receipt.json','receipt_sha256':s.digest(receipt)}])
        child['failure_recovery_audit']=[{'receipt_sha256':s.digest(receipt)}]
        s.save_json(self.old.host(entry['directory']+'/plan.json'),plan)
        import_policy=s.read_json(self.old.host(entry['directory']+'/import-policy.json'))
        import_policy['day_policy_sha256']=s.digest(policy)
        entry['import_policy_sha256']=s.digest(import_policy)
        os.chmod(self.old.host(entry['directory']+'/import-policy.json'),0o600)
        s.save_json(self.old.host(entry['directory']+'/import-policy.json'),import_policy)
        rag_import=self.old.rag_import_policy(entry,plan)
        entry['rag_import_policy_sha256']=s.digest(rag_import)
        os.chmod(self.old.host(entry['directory']+'/rag-import-policy.json'),0o600)
        s.save_json(self.old.host(entry['directory']+'/rag-import-policy.json'),rag_import)
        s.atomic_bytes(self.old.host(entry['directory']+'/runtime.env'),self.old.env_bytes(entry,entry['plan_sha256'],entry['import_policy_sha256']))
        s.save_json(self.old.host(entry['checkpoint']),child)
        self.old.save(journal)
        self.original=copy.deepcopy(child)
        self.old_policy=f.release/'mixed-policy.json'; s.save_json(self.old_policy,f.policy)
        self.new_policy=f.release/'success-target-policy.json'; self.backup=f.root/'limit-backup'
        self.probes=[]
        self.migration=self.make()
        self.plan=self.migration.plan(); self.plan_sha=s.digest(self.plan)

    def quiet(self):
        self.probes.append('read-only')
        return {'writers_stopped':True,'broker_empty':True,'active_jobs':0}

    def make(self):
        return m.Migration(self.old,self.new_policy,self.backup,old_policy_path=self.old_policy,
            reason='User approved removal of the shared attempt cap',approved_at=self.f.now.isoformat(),quiescent=self.quiet)

    def snapshot(self):
        return {p.relative_to(self.f.state).as_posix():p.read_bytes() for p in self.f.state.rglob('*') if p.is_file()}

    def test_plan_changes_nothing_and_has_no_raw_records_credentials_or_commands(self):
        before=self.snapshot(); calls=len(self.f.calls)
        self.assertEqual(self.migration.plan(),self.plan)
        self.assertEqual(self.snapshot(),before); self.assertEqual(len(self.f.calls),calls)
        self.assertFalse(self.backup.exists()); self.assertFalse(self.new_policy.exists())
        raw=json.dumps(self.plan)
        self.assertNotIn('synthetic',raw); self.assertNotIn('https://example.invalid',raw)
        self.assertEqual((self.plan['revision']['prior_daily_attempts'],self.plan['revision']['prior_daily_successes']),(9,6))

    def test_apply_preserves_all_32_records_costs_policy_seen_recovery_and_replays(self):
        before=self.snapshot(); calls=len(self.f.calls)
        report=self.migration.apply(self.plan,self.plan_sha)
        self.assertEqual((report['daily_attempts'],report['daily_passes'],report['max_attempts']),(9,6,None))
        self.assertEqual(report['phase'],'reconciled_paused'); self.assertFalse(report['generation_enabled'])
        self.assertEqual(len(self.f.calls),calls)
        entry=self.migration.new.load_journal()['days'][-1]
        child=s.read_json(self.old.host(entry['checkpoint']))
        revision=child.pop('daily_limit_revisions')
        self.assertEqual(child,self.original); self.assertEqual(len(child['records']),32)
        self.assertEqual(revision,[self.plan['revision']])
        revision_file=self.old.host(entry['directory']+'/rag-inbox/.approvals/daily-limit-revision.json')
        self.assertEqual(s.read_json(revision_file),revision[0])
        self.assertEqual(revision_file.read_bytes(),json.dumps(revision[0],sort_keys=True,
            separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
        changed={name for name,raw in before.items() if self.snapshot()[name]!=raw}
        self.assertEqual(changed,set(self.migration.names(self.plan['source_journal'])[:2]))
        self.assertEqual(s.read_json(self.old_policy),self.old.policy)
        self.assertEqual(s.read_json(self.new_policy),{**self.old.policy,'schema_version':3,'max_attempts':None})
        after=self.snapshot(); receipt=(self.backup/'receipt.json').read_bytes()
        self.assertEqual(self.make().apply(self.plan,self.plan_sha),report)
        self.assertEqual(self.snapshot(),after); self.assertEqual((self.backup/'receipt.json').read_bytes(),receipt)
        self.assertEqual(len(self.migration.new.load_journal()['daily_limit_approval_history']),1)

    def test_crash_after_checkpoint_before_journal_finishes_from_exact_backup(self):
        actual=s.atomic_bytes
        def crash(path,raw,**kwargs):
            if path==self.old.journal_path: raise fixtures.SimulatedCrash('journal')
            return actual(path,raw,**kwargs)
        with patch.object(s,'atomic_bytes',crash),self.assertRaises(fixtures.SimulatedCrash):
            self.migration.apply(self.plan,self.plan_sha)
        self.assertTrue(self.new_policy.exists())
        with self.assertRaises(s.ScheduleError):self.migration.new.load_journal()
        report=self.make().apply(self.plan,self.plan_sha)
        self.assertEqual(report['status'],'success_only_migrated_inactive')
        self.assertEqual(s.read_json(self.backup/'before-0.bin'),self.original)

    def test_tampered_cost_receipt_policy_or_stale_digest_cannot_write(self):
        for which in ('checkpoint','receipt','policy','digest'):
            with self.subTest(which=which):
                target={'checkpoint':self.old.host(self.plan['source_journal']['days'][-1]['checkpoint']),
                    'receipt':self.f.state/'failure-receipt.json','policy':self.old_policy}.get(which)
                original=target.read_bytes() if target else None
                if target:target.write_bytes(original+b' ')
                before=self.snapshot()
                with self.assertRaises(s.ScheduleError):
                    self.migration.apply(self.plan,'0'*64 if which=='digest' else self.plan_sha)
                self.assertEqual(self.snapshot(),before); self.assertFalse(self.backup.exists())
                self.assertFalse(self.new_policy.exists())
                if target:target.write_bytes(original)

    def test_active_writers_broker_or_jobs_block_before_backup(self):
        for changed in ({'writers_stopped':False},{'broker_empty':False},{'active_jobs':1},{'active_jobs':False}):
            self.migration.quiescent=lambda:{'writers_stopped':True,'broker_empty':True,'active_jobs':0,**changed}
            with self.assertRaisesRegex(s.ScheduleError,'limit_migration_requires_quiescence'):
                self.migration.apply(self.plan,self.plan_sha)
            self.assertFalse(self.backup.exists()); self.assertFalse(self.new_policy.exists())

    def test_changed_backup_or_revision_file_cannot_be_replayed(self):
        self.migration.apply(self.plan,self.plan_sha)
        for path in (self.backup/'before-0.bin',self.old.host(self.migration.names(self.plan['source_journal'])[2])):
            os.chmod(path,0o600)
            original=path.read_bytes(); path.write_bytes(original+b' ')
            before=self.snapshot()
            with self.assertRaises(s.ScheduleError):self.make().apply(self.plan,self.plan_sha)
            self.assertEqual(self.snapshot(),before);path.write_bytes(original)


if __name__=='__main__':unittest.main()
