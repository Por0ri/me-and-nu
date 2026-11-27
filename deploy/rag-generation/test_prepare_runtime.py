"""File-only runtime preparation; no real key, source data, DB or provider."""
import contextlib
import copy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prepare_runtime as prepare

HERE = Path(__file__).resolve().parent


def template():
    return {'isolated_root':'/var/lib/menu-rag','database_path':'/var/lib/menu-rag/ani_rag.sqlite3',
        'image_root':'/opt/rag-images','api_key_file':'/run/secrets/rag-llm-key-NOT-MOUNTED',
        'vector_dsn_file':'/run/secrets/rag-vector-dsn','expected_vector_database':'menu_rag_vector',
        'catalog_work_ids':[1,2,5,7,8,9,10,11],'enabled':False,'confirm_isolated_copy':True,
        'model':'old-model','pricing':{},'offline':False}


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.template_file = self.root/'template.json'
        self.template_file.write_bytes(prepare.runtime_bytes(template()))
        self.model_file = HERE/'model-settings-20261126.json'
        self.settings,self.model_raw = prepare.read_json(self.model_file)
        self.kwargs = {'template_path':self.template_file,'model_settings_path':self.model_file,
            'reviewed_model_settings_sha256':prepare.raw_sha(self.model_raw)}

    def test_disabled_runtime_prepared_fresh_and_original_preserved(self):
        old = self.template_file.read_bytes()
        output = self.root/'disabled.json'
        report = prepare.prepare(**self.kwargs,output=output)
        runtime = json.loads(output.read_bytes())
        self.assertFalse(runtime['enabled'])
        self.assertEqual(runtime['model'],'gpt-6-luna')
        self.assertEqual(runtime['api_key_file'],'/run/secrets/rag-llm-key')
        self.assertEqual(runtime['pricing'],self.settings['pricing'])
        self.assertEqual(report['runtime_sha256'],prepare.raw_sha(output.read_bytes()))
        self.assertEqual(report['llm_calls'],0)
        self.assertFalse(report['key_contents_read'])
        self.assertFalse(report['provider_generation_permission_verified'])
        self.assertEqual(self.template_file.read_bytes(),old)
        with self.assertRaisesRegex(prepare.PreparationError,'fresh_output'):
            prepare.prepare(**self.kwargs,output=output)

    def test_hash_only_preview_writes_nothing_and_exposes_no_key_material(self):
        before = set(self.root.iterdir())
        report = prepare.prepare(**self.kwargs)
        self.assertEqual(set(self.root.iterdir()),before)
        self.assertFalse(report['enabled'])
        self.assertNotEqual(report['runtime_sha256'],report['enabled_candidate_sha256'])
        self.assertEqual(report['key_host_reference'],prepare.KEY_HOST)

    def test_model_file_hash_required_and_missing_prices_unknown_models_rejected(self):
        with self.assertRaisesRegex(prepare.PreparationError,'reviewed_model_settings'):
            prepare.prepare(**{**self.kwargs,'reviewed_model_settings_sha256':'0'*64})
        for fault in ('missing','nan','negative','bool','unknown','source'):
            settings = copy.deepcopy(self.settings)
            if fault == 'missing': del settings['pricing']['gpt-6-luna']['output']
            if fault == 'nan': settings['pricing']['gpt-6-luna']['output'] = 'NaN'
            if fault == 'negative': settings['pricing']['gpt-6-luna']['input'] = '-1'
            if fault == 'bool': settings['pricing']['gpt-6-luna']['input'] = True
            if fault == 'unknown': settings['pricing']['unreviewed-snapshot'] = settings['pricing']['gpt-6-luna']
            if fault == 'source': settings['pricing_sources'][0]['url'] = 'https://unreviewed.example/pricing'
            with self.subTest(fault=fault),self.assertRaises(prepare.PreparationError):
                prepare.build_runtime(template(),settings)

    def test_template_requires_isolated_paths_and_preserves_work_ids(self):
        for fault,value in [('database_path','/service/database.sqlite3'),('confirm_isolated_copy',False),('offline',True),
                            ('catalog_work_ids',[1,1])]:
            row = template()
            row[fault] = value
            with self.subTest(fault=fault),self.assertRaises(prepare.PreparationError):
                prepare.build_runtime(row,self.settings)
        self.assertEqual(prepare.build_runtime(template(),self.settings)['catalog_work_ids'],template()['catalog_work_ids'])

    def enabled_policy(self,output):
        runtime = prepare.build_runtime(template(),self.settings,enabled=True)
        return {'schema_version':2,'purpose':'daily_count_schedule','max_attempts':40,'success_target':20,
            'rag':{'enabled':True,'scope_file':'reviewed-rag-scope.json','scope_sha256':'a'*64,
                'runtime_config':str(output),'runtime_sha256':prepare.raw_sha(prepare.runtime_bytes(runtime)),
                'feed_valid_days':7}}

    def test_enabled_file_requires_explicit_optin_reviewed_policy_and_exact_runtime(self):
        output = self.root/'enabled.json'
        policy = self.enabled_policy(output)
        path = self.root/'policy.json'
        path.write_bytes(prepare.runtime_bytes(policy))
        with self.assertRaisesRegex(prepare.PreparationError,'require_enable'):
            prepare.prepare(**self.kwargs,output=output,policy_path=path,reviewed_policy_sha256=prepare.digest(policy))
        for fault in ('sha','flag','runtime','path','limits'):
            row = copy.deepcopy(policy)
            if fault == 'flag': row['rag']['enabled'] = False
            if fault == 'runtime': row['rag']['runtime_sha256'] = 'b'*64
            if fault == 'path': row['rag']['runtime_config'] = str(self.root/'wrong.json')
            if fault == 'limits': row['success_target'] = 21
            path.write_bytes(prepare.runtime_bytes(row))
            sha = '0'*64 if fault == 'sha' else prepare.digest(row)
            with self.subTest(fault=fault),self.assertRaises(prepare.PreparationError):
                prepare.prepare(**self.kwargs,output=output,enable=True,policy_path=path,reviewed_policy_sha256=sha)
            self.assertFalse(output.exists())
        path.write_bytes(prepare.runtime_bytes(policy))
        report = prepare.prepare(**self.kwargs,output=output,enable=True,policy_path=path,reviewed_policy_sha256=prepare.digest(policy))
        self.assertTrue(json.loads(output.read_bytes())['enabled'])
        self.assertTrue(report['enabled'])
        self.assertFalse(report['worker_started'])

    def test_host_stat_only_key_check_and_delete_journal_are_readonly(self):
        state = self.root/'state'
        images = self.root/'images'
        state.mkdir(); images.mkdir()
        key,dsn = self.root/'synthetic-key',self.root/'synthetic-dsn'
        key.write_bytes(b'never-open-this-placeholder')
        dsn.write_bytes(b'never-open-this-placeholder')
        db = state/'ani_rag.sqlite3'
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE fixture(id INTEGER)')
            conn.commit()
        before = db.read_bytes()
        original_open = Path.open
        def guarded_open(path,*args,**kwargs):
            if path in (key,dsn): raise AssertionError('secret contents opened')
            return original_open(path,*args,**kwargs)
        with patch.object(prepare,'KEY_HOST',str(key)),patch.object(Path,'open',guarded_open),\
                patch.object(prepare,'validate_secret_permissions'):
            report = prepare.check_host_files(state_dir=state,image_dir=images,vector_dsn_file=dsn,key_file=key)
        self.assertEqual(report['sqlite_journal_mode'],'delete')
        self.assertFalse(report['key_contents_read'])
        self.assertEqual(db.read_bytes(),before)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
        with patch.object(prepare,'KEY_HOST',str(key)),patch.object(prepare,'validate_secret_permissions'),\
                self.assertRaisesRegex(prepare.PreparationError,'delete_journal'):
            prepare.check_host_files(state_dir=state,image_dir=images,vector_dsn_file=dsn,key_file=key)

    def test_secret_owner_and_permissions_must_match_worker(self):
        prepare.validate_secret_permissions(SimpleNamespace(st_uid=10001,st_mode=0o100600),{0o600})
        for uid,mode in ((0,0o600),(10001,0o644),(10001,0o777),(10001,0o400)):
            with self.subTest(uid=uid,mode=mode),self.assertRaises(prepare.PreparationError):
                prepare.validate_secret_permissions(SimpleNamespace(st_uid=uid,st_mode=mode),{0o600})


if __name__ == '__main__':
    unittest.main()
