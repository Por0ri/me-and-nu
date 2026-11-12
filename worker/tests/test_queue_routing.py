"""Keep local probes and paid generation on separate queues and brokers."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


WORKER_ROOT = Path(__file__).resolve().parents[1]


def run_python(source, *arguments, overrides=None):
    # Each registry check needs fresh Celery import/finalization state.
    env = {key: value for key, value in os.environ.items() if not key.startswith("CELERY_")}
    env.update(overrides or {})
    return subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source), *arguments],
        cwd=WORKER_ROOT, env=env, capture_output=True, text=True, timeout=20,
    )


class QueueRoutingTests(unittest.TestCase):
    def test_registries_are_disjoint_in_both_import_orders(self):
        script = """
            from importlib import import_module
            import sys

            apps = {}
            for module in sys.argv[1:]:
                app = import_module('menu_worker.' + module).app
                apps[module] = app
                app.loader.import_default_modules()

            fake, anime, live = apps['app'], apps['anime_app'], apps['generation_app']
            def project_tasks(app):
                return {name for name in app.tasks if name.startswith(('runtime.', 'content.'))}

            assert project_tasks(fake) == {'runtime.probe', 'runtime.heartbeat', 'content.fake_generate'}
            assert project_tasks(anime) == {'content.anime_probe'}
            assert project_tasks(live) == {'content.anime_generate'}
            assert set(fake.amqp.queues) == {'runtime', 'content-generation'}
            assert set(anime.amqp.queues) == {'anime-generation'}
            assert set(live.amqp.queues) == {'anime-generation-live'}
            assert fake.amqp.router.route({}, 'content.fake_generate')['queue'].name == 'content-generation'
            assert anime.amqp.router.route({}, 'content.anime_probe')['queue'].name == 'anime-generation'
            assert live.amqp.router.route({}, 'content.anime_generate')['queue'].name == 'anime-generation-live'
            assert set(fake.conf.beat_schedule) == {'runtime-heartbeat'}
            assert not anime.conf.beat_schedule
            assert not live.conf.beat_schedule
            assert fake.conf.broker_url == 'redis://redis-tasks:6379/0'
            assert anime.conf.broker_url == 'redis://anime-redis:6379/0'
            assert live.conf.broker_url == 'redis://generation-redis:6379/0'
            assert fake.conf.result_backend == 'redis://redis-tasks:6379/1'
            assert anime.conf.result_backend == 'redis://anime-redis:6379/1'
            assert live.conf.result_backend == 'redis://generation-redis:6379/1'
            assert live.conf.task_soft_time_limit == 3660
            assert live.conf.task_time_limit == 3720
            assert live.conf.visibility_timeout == 3900
            assert live.conf.broker_transport_options['visibility_timeout'] == 3900
            assert live.conf.result_backend_transport_options['visibility_timeout'] == 3900
            assert fake.conf.visibility_timeout == anime.conf.visibility_timeout == 90
            task = live.tasks['content.anime_generate']
            assert task.soft_time_limit in (None, live.conf.task_soft_time_limit)
            assert task.time_limit in (None, live.conf.task_time_limit)
        """
        for order in (("app", "anime_app", "generation_app"), ("generation_app", "anime_app", "app")):
            with self.subTest(order=order):
                result = run_python(script, *order)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_anime_app_refuses_fake_broker_or_result_store(self):
        for key, value in (("CELERY_BROKER_URL", "redis://redis-tasks:6379/0"),
                           ("CELERY_RESULT_BACKEND", "redis://redis-tasks:6379/1")):
            with self.subTest(key=key):
                result = run_python("import menu_worker.anime_app", overrides={key: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("dedicated anime-redis service", result.stderr)

    def test_generation_app_refuses_probe_broker_or_result_store(self):
        for key, value in (("CELERY_BROKER_URL", "redis://anime-redis:6379/0"),
                           ("CELERY_RESULT_BACKEND", "redis://redis-tasks:6379/1")):
            with self.subTest(key=key):
                result = run_python("import menu_worker.generation_app", overrides={key: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("dedicated generation-redis service", result.stderr)

    def test_anime_worker_health_requires_its_node_and_broker(self):
        script = """
            from importlib import import_module
            import sys
            from unittest.mock import patch
            from menu_worker import health

            module, role, prefix = sys.argv[1:]
            worker = import_module('menu_worker.' + module).app
            name = prefix + '@node-1'
            with patch('socket.gethostname', return_value='node-1'), \\
                 patch.object(health.app.control, 'ping', side_effect=AssertionError('wrong broker')), \\
                 patch.object(worker.control, 'ping') as ping:
                ping.return_value = [{'content@node-1': {'ok': 'pong'}}]
                assert not health.check(role)
                ping.return_value = [{name: {'ok': 'pong'}}]
                assert health.check(role)
                ping.assert_called_with(destination=[name], timeout=3)
        """
        for values in (("anime_app", "anime-worker", "anime"), ("generation_app", "generation-worker", "generate")):
            with self.subTest(role=values[1]):
                result = run_python(script, *values)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
