"""Only one Beat may own a schedule volume, including across containers."""
import fcntl

from celery.beat import PersistentScheduler


class SingleBeatScheduler(PersistentScheduler):
    def setup_schedule(self):
        self._runtime_lock = open(f"{self.schedule_filename}.lock", "a")
        try:
            fcntl.flock(self._runtime_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._runtime_lock.close()
            raise RuntimeError("Another Beat already owns this runtime schedule volume") from None
        try:
            super().setup_schedule()
        except BaseException:
            self._runtime_lock.close()
            raise

    def close(self):
        try:
            super().close()
        finally:
            lock = getattr(self, "_runtime_lock", None)
            if lock is not None:
                lock.close()
