"""Optional API-hosted executor for the durable outbox, in every environment."""

import logging
import time
from threading import Event, Thread

from app.workers.processor import cleanup, expire_interviews, process_available

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    def __init__(self, runtime):
        self.runtime = runtime
        self.stopped = Event()
        self.thread = None
        self.next_expiry = time.monotonic() + 30
        self.next_cleanup = time.monotonic() + 3600

    def start(self):
        self.thread = Thread(target=self.run, name="interview-outbox", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run_once(self):
        count = process_available(self.runtime, limit=10, include_queued=True)
        now = time.monotonic()
        if now >= self.next_expiry:
            expire_interviews(self.runtime)
            self.next_expiry = now + 30
        if now >= self.next_cleanup:
            cleanup(self.runtime)
            self.next_cleanup = now + 3600
        return count

    def run(self):
        while not self.stopped.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.warning("background_tasks_failed", extra={"error_type": type(exc).__name__})
            self.stopped.wait(self.runtime.settings.task_poll_seconds)
