"""Resolve the mixed scheduler overlays; start no containers or providers."""
import copy
from pathlib import Path
import unittest

from test_inactive_deploy import resolved, mounts, assert_pinned_images

HERE = Path(__file__).resolve().parent
DAILY = HERE.parent/'daily-generation'


class MixedScheduleComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = {'DAILY_SOURCE_CHECKPOINT':'/runtime/state/seed.json','DAILY_SOURCE_SHA256':'a'*64,
            'DAILY_CHECKPOINT':'/runtime/state/scheduled/2026-11-26/checkpoint.json','DAILY_POLICY_DATE':'2026-11-26',
            'DAILY_PLAN_SHA256':'b'*64,'DAILY_IMPORT_POLICY_SHA256':'c'*64,
            'ANIME_LLM_KEY_HOST_FILE':'/approved/article-existing-key',
            'ANIME_COVER_HISTORY_BOOTSTRAP_HOST_FILE':'/approved/cover-history.json',
            'ANIME_COVER_HISTORY_BOOTSTRAP_SHA256':'d'*64,
            'RAG_IMPORT_POLICY_HOST_FILE':'/prepared/scheduled/2026-11-26/rag-import-policy.json',
            'RAG_SERVICE_DSN_HOST_FILE':'/prepared/secrets/service-dsn',
            'RAG_INBOX_NAME':'scheduled/2026-11-26/rag-inbox',
            # Host-side policy chooses the active overlay, never this ambient flag.
            'RAG_GENERATION_ENABLED':'true'}
        cls.article = resolved([DAILY/'compose.runtime.yaml',DAILY/'compose.approved.yaml'],overrides=values)
        cls.disabled = resolved([DAILY/'compose.runtime.yaml',DAILY/'compose.approved.yaml',HERE/'compose.scheduled.yaml'],overrides=values)
        cls.active = resolved([DAILY/'compose.runtime.yaml',DAILY/'compose.approved.yaml',HERE/'compose.scheduled.yaml',
            HERE/'compose.scheduled.active.yaml'],overrides=values)

    def test_disabled_rag_has_no_key_egress_or_running_command(self):
        worker = self.disabled['services']['rag-worker']
        self.assertEqual(worker['environment']['RAG_GENERATION_ENABLED'],'false')
        self.assertEqual(self.disabled['services']['daily-controller']['environment']['RAG_GENERATION_ENABLED'],'false')
        self.assertEqual(worker['command'][-1],'--help')
        self.assertEqual(set(worker['networks']),{'tasks','rag-vector'})
        self.assertNotIn('/run/secrets/rag-llm-key',mounts(worker))
        self.assertEqual(self.disabled['services']['rag-pending-importer']['command'],['--help'])

    def test_existing_article_worker_and_importer_unchanged(self):
        for config in (self.disabled,self.active):
            for name in ('generation-worker','pending-importer'):
                self.assertEqual(config['services'][name],self.article['services'][name])
            old,new = self.article['services']['daily-controller'],config['services']['daily-controller']
            for key in ('image','command','entrypoint','networks','profiles','restart'):
                self.assertEqual(old[key],new[key])
            self.assertTrue(all(mounts(new)[key] == value for key,value in mounts(old).items()))

    def test_existing_key_is_worker_only_and_readonly_after_explicit_overlay(self):
        worker = self.active['services']['rag-worker']
        self.assertEqual(worker['environment']['RAG_GENERATION_ENABLED'],'true')
        self.assertEqual(self.active['services']['daily-controller']['environment']['RAG_GENERATION_ENABLED'],'true')
        self.assertEqual(set(worker['networks']),{'tasks','rag-vector','generation-egress'})
        secret = mounts(worker)['/run/secrets/rag-llm-key']
        self.assertEqual(secret['source'],'/home/ubuntu/.secrets/anime-openai-key.txt')
        self.assertTrue(secret['read_only'])
        for name in ('daily-controller','rag-pending-importer','pending-importer'):
            self.assertNotIn('/run/secrets/rag-llm-key',mounts(self.active['services'][name]))
            self.assertFalse(any(v['source'] == secret['source'] for v in mounts(self.active['services'][name]).values()))
        self.assertIn('--concurrency=1',worker['command'])
        self.assertIn('--queues=content.rag_generate',worker['command'])
        self.assertEqual(worker['healthcheck']['test'][-2:],'--destination=rag@scheduled --timeout=5'.split())

    def test_controller_has_readonly_dictionary_and_runtime_no_vector_secret(self):
        for config in (self.disabled,self.active):
            volumes = mounts(config['services']['daily-controller'])
            self.assertTrue(volumes['/var/lib/menu-rag']['read_only'])
            self.assertTrue(volumes['/run/rag/runtime.json']['read_only'])
            self.assertNotIn('/run/secrets/rag-vector-dsn',volumes)
            self.assertNotIn('/opt/rag-images',volumes)

    def test_separate_day_inbox_and_import_policy_dsn_are_readonly(self):
        for config in (self.disabled,self.active):
            service = config['services']['rag-pending-importer']
            volumes = mounts(service)
            self.assertEqual(set(volumes),{'/var/lib/menu-jobs','/runtime/state/scheduled/2026-11-26/rag-inbox',
                '/runtime/rag-import-policy.json','/run/secrets/rag-service-dsn'})
            self.assertTrue(all(row['read_only'] for row in volumes.values()))
            self.assertNotEqual(volumes['/runtime/state/scheduled/2026-11-26/rag-inbox']['source'],
                                mounts(config['services']['pending-importer'])['/runtime/inbox']['source'])
            self.assertEqual(service['environment']['DATABASE_URL'],'postgresql+psycopg://offline@127.0.0.1:1/offline_not_used')
            self.assertEqual(service['command'],['--help'])

    def test_new_services_digest_pinned_ports_closed_and_env_keys_empty(self):
        for config in (self.disabled,self.active):
            subset = {'services':{name:config['services'][name] for name in ('rag-worker','rag-pending-importer')}}
            assert_pinned_images(subset)
            for row in subset['services'].values():
                self.assertEqual(row['profiles'],['manual'])
                self.assertTrue(row['read_only'])
                self.assertEqual(row['user'],'10001:10001')
                self.assertEqual(row['restart'],'no')
                self.assertFalse(row.get('ports'))
                self.assertTrue(all(row['environment'][key] == '' for key in ('OPENAI_API_KEY','LLM_KEY')))
                self.assertTrue(all(v['type']=='bind' and v['bind']['create_host_path'] is False for v in row['volumes']))


if __name__ == '__main__':
    unittest.main()
