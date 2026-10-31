"""Run scheduled cleanup once in a local/eager development environment."""

from app.runtime import Runtime
from app.workers.processor import cleanup, expire_interviews

if __name__ == "__main__":
    runtime = Runtime()
    try:
        cleanup(runtime)
        expire_interviews(runtime)
        if runtime.settings.task_mode == "eager":
            runtime.drain()
    finally:
        runtime.db.engine.dispose()
