"""Real Celery routing over memory transport; no Worker, Redis, DB or provider."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from queue import Empty
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from celery import Celery
from celery.exceptions import QueueNotFound
from kombu.transport.memory import Channel

from menu_worker.bounded_generation import StopRun
from menu_worker.bounded_resume import publish_with_deadline
from menu_worker.generation_app import app as production_article
from menu_worker.rag_app import app as production_rag
from menu_worker.rag_daily_runtime import compose_runtime
from menu_worker.rag_tasks import QUEUE_NAME, TASK_NAME


class RagPublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # Clone the real application queue/serializer settings, then replace
        # only transport/backend. Production app settings stay untouched.
        self.article = Celery('article_memory_test', set_as_current=False)
        self.rag = Celery('rag_memory_test', set_as_current=False)
        for app, production in ((self.article, production_article), (self.rag, production_rag)):
            app.conf.update({**dict(production.conf), 'broker_url': 'memory://', 'result_backend': 'cache+memory://'})
            self.addCleanup(app.close)
        Channel.queues.clear()
        self.addCleanup(Channel.queues.clear)
        self.database = self.root / 'isolated.sqlite'
        self.database.write_bytes(b'not opened by publisher composition')
        self.runtime_file = self.root / 'runtime.json'
        self.values = {'isolated_root': str(self.root), 'database_path': str(self.database),
            'image_root': str(self.root), 'api_key_file': '/not/read/key', 'vector_dsn_file': '/not/read/dsn',
            'expected_vector_database': 'rag_isolated', 'catalog_work_ids': [8], 'enabled': True,
            'confirm_isolated_copy': True, 'offline': False,
            'pricing': {model: {'input': '1', 'output': '1', 'cached_input': '0.1'}
                        for model in ('gpt-6-luna', 'text-embedding-3-small')}}
        self.runtime_file.write_text(json.dumps(self.values))
        self.sha = hashlib.sha256(self.runtime_file.read_bytes()).hexdigest()
        self.deadline = '2099-01-01T00:00:00+09:00'
        self.plan = {'rag_runtime_sha256': self.sha, 'rag_scope': {'routes': [{'menu_work_id': 8}]},
            'config': {'artifact_root': str(self.root), 'deadline': self.deadline, 'job_timeout_seconds': 1500},
            'rag_delivery_root': str(self.root), 'daily_policy': {}}
        self.key = str(uuid4())

    @contextmanager
    def runtime(self, *, enabled=True):
        with patch.dict('os.environ', {'RAG_GENERATION_ENABLED': 'true', 'RAG_RUNTIME_CONFIG_FILE': str(self.runtime_file)}), \
             patch('menu_worker.rag_store.RagStore') as store, patch('menu_worker.rag_app.app', self.rag):
            yield compose_runtime(self.plan, app=self.article, runtime_path=self.runtime_file,
                                  runtime_sha256=self.sha, enable=enabled)
            store.return_value.submit.assert_not_called()
            store.return_value.claim.assert_not_called()

    def message(self, app, queue):
        with app.connection_for_read() as connection:
            target = connection.SimpleQueue(queue)
            try:
                message = target.get(block=False)
                result = message.headers, message.payload
                message.ack()
                return result
            finally:
                target.close()

    def test_article_publisher_reproduces_missing_rag_queue_without_broker(self):
        self.assertFalse(self.article.conf.task_create_missing_queues)
        self.assertEqual(set(self.article.amqp.queues), {'anime-generation-live'})
        self.assertEqual(set(self.rag.amqp.queues), {QUEUE_NAME})
        with self.assertRaises(QueueNotFound):
            publish_with_deadline(self.key, app=self.article, task=TASK_NAME, queue=QUEUE_NAME,
                                  deadline=self.deadline, job_timeout_seconds=1500)
        self.assertFalse(Channel.queues)

    def test_composed_rag_publisher_delivers_once_to_own_queue_and_keeps_article_route(self):
        before_article, before_rag = set(self.article.amqp.queues), set(self.rag.amqp.queues)
        with self.runtime() as runtime:
            task_id = runtime.publish(self.key)
        self.assertIsInstance(task_id, str)
        headers, payload = self.message(self.rag, QUEUE_NAME)
        self.assertEqual(headers['task'], TASK_NAME)
        self.assertEqual(payload[1], {'job_id': self.key})
        self.assertIsNotNone(headers['expires'])
        with self.assertRaises(Empty): self.message(self.rag, QUEUE_NAME)
        with self.assertRaises(Empty): self.message(self.article, 'anime-generation-live')
        publish_with_deadline(self.key, app=self.article, task='content.anime_generate', queue='anime-generation-live',
                              deadline=self.deadline, job_timeout_seconds=1500)
        self.assertEqual(self.message(self.article, 'anime-generation-live')[0]['task'], 'content.anime_generate')
        self.assertEqual(set(self.article.amqp.queues), before_article)
        self.assertEqual(set(self.rag.amqp.queues), before_rag)
        self.assertFalse(self.article.conf.task_create_missing_queues)
        self.assertFalse(self.rag.conf.task_create_missing_queues)

    def test_disabled_or_changed_runtime_and_mismatched_broker_publish_nothing(self):
        with self.runtime(enabled=False) as runtime:
            with self.assertRaisesRegex(StopRun, 'rag_generation_disabled'): runtime.publish(self.key)
        with self.runtime() as runtime:
            self.rag.conf.broker_url = 'memory://unreviewed'
            with self.assertRaisesRegex(StopRun, 'rag_publisher_broker_mismatch'): runtime.publish(self.key)
            self.rag.conf.broker_url = 'memory://'
            self.runtime_file.write_bytes(self.runtime_file.read_bytes() + b' ')
            with self.assertRaisesRegex(StopRun, 'rag_runtime_not_reviewed'): runtime.publish(self.key)
        self.assertFalse(Channel.queues)


if __name__ == '__main__':
    unittest.main()
