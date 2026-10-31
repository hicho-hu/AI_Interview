"""Start an isolated local HTTP listener, run smoke requests, and stop the listener."""

import os
import subprocess
import sys
import time

import httpx
import smoke

if __name__ == "__main__":
    if os.environ.get("ENVIRONMENT", "development") == "production":
        raise SystemExit("Only run this development smoke check with development configuration.")
    base = "http://127.0.0.1:8765"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError("Development HTTP server failed to start")
            try:
                if httpx.get(base + "/health/ready", timeout=1).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise TimeoutError("Development HTTP server was not ready")
        smoke.base = base
        smoke.main()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
