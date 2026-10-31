"""Start the local MySQL/Redis/API/Worker/Beat stack without Docker."""

import fcntl
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "var" / "run"
LOGS = ROOT / "var" / "logs"
PYTHON = ROOT / ".venv" / "bin" / "python"
STATE = RUN / "services.json"


def listening(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def wait_until(check, label, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.4)
    raise RuntimeError(f"{label} 未能就绪，请检查 {LOGS}")


def media_tools(settings):
    for name in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [getattr(settings, name + "_bin"), "-version"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"{name} 不可用，语音/视频上传需要 ffmpeg 和 ffprobe。"
                "请安装 FFmpeg，或在 .env 配置 FFMPEG_BIN、FFPROBE_BIN 的可执行文件路径。"
            ) from exc
    print("FFmpeg / FFprobe 已就绪", flush=True)


def dependencies(settings):
    db_url = make_url(settings.database_url)
    db_host, db_port = db_url.host or "127.0.0.1", db_url.port or 3306
    if not listening(db_host, db_port):
        mysqld = Path("/opt/local/lib/mysql8/bin/mysqld")
        data = Path("/opt/local/var/db/mysql8")
        if (
            db_host not in ("127.0.0.1", "localhost")
            or db_port != 3306
            or not mysqld.exists()
            or not data.is_dir()
        ):
            raise RuntimeError("MySQL 未运行，请先启动 .env 中 DATABASE_URL 对应的数据库")
        command = [
            str(mysqld),
            "--defaults-file=/opt/local/etc/mysql8/my.cnf",
            "--datadir=" + str(data),
            "--user=_mysql",
            "--daemonize",
        ]
        if os.geteuid() != 0:
            command.insert(0, "sudo")
        print("启动本机 MySQL（可能需要输入本机管理员密码）…", flush=True)
        with (LOGS / "mysql.log").open("ab") as log:
            subprocess.run(command, stdout=log, stderr=log, check=True)
        wait_until(lambda: listening(db_host, db_port), "MySQL")
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise RuntimeError("MySQL 连接失败，请检查 .env 中的数据库名称及账号") from None
    finally:
        engine.dispose()
    print("MySQL 已就绪", flush=True)

    if not settings.redis_url:
        raise RuntimeError("异步任务需要在 .env 中配置 REDIS_URL")
    redis_url = urlsplit(settings.redis_url)
    redis_host, redis_port = redis_url.hostname or "127.0.0.1", redis_url.port or 6379
    if not listening(redis_host, redis_port):
        server = shutil.which("redis-server")
        if (
            redis_host not in ("127.0.0.1", "localhost")
            or redis_url.scheme != "redis"
            or redis_url.password
            or not server
        ):
            raise RuntimeError("Redis 未运行，请先启动 .env 中 REDIS_URL 对应的服务")
        data = ROOT / "var" / "redis"
        data.mkdir(exist_ok=True)
        subprocess.run(
            [
                server,
                "--bind",
                "127.0.0.1",
                "--port",
                str(redis_port),
                "--daemonize",
                "yes",
                "--dir",
                str(data),
                "--appendonly",
                "yes",
                "--pidfile",
                str(RUN / "redis.pid"),
                "--logfile",
                str(LOGS / "redis.log"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        wait_until(lambda: listening(redis_host, redis_port), "Redis")
    with Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3) as redis:
        try:
            redis.ping()
        except RedisError:
            raise RuntimeError("Redis 连接失败，请检查 REDIS_URL") from None
    print("Redis 已就绪", flush=True)


def matches(pid, role):
    if not isinstance(pid, int) or pid <= 0:
        return False
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    command = result.stdout
    marker = "app.main:app" if role == "api" else "app.workers.celery_app:celery_app"
    return str(PYTHON) in command and marker in command and (role == "api" or f" {role} " in command)


def start_apps():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    commands = {
        "api": [
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "info",
        ],
        "worker": [
            "-m",
            "celery",
            "-A",
            "app.workers.celery_app:celery_app",
            "worker",
            "--loglevel=info",
            "--pool=threads",
            "--concurrency=2",
            "--hostname=ai-interview@%h",
            "--pidfile=" + str(RUN / "worker.pid"),
        ],
        "beat": [
            "-m",
            "celery",
            "-A",
            "app.workers.celery_app:celery_app",
            "beat",
            "--loglevel=info",
            "--schedule=" + str(RUN / "celerybeat"),
            "--pidfile=" + str(RUN / "beat.pid"),
        ],
    }
    env = dict(os.environ, TASK_MODE="worker", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    for role, args in commands.items():
        if matches(state.get(role, {}).get("pid"), role):
            print(f"{role} 已在运行（PID {state[role]['pid']}）", flush=True)
            continue
        if role == "api" and listening("127.0.0.1", 8000):
            raise RuntimeError("8000 端口已被其他进程占用，请先确认端口使用情况")
        with (LOGS / f"{role}.log").open("ab") as log:
            process = subprocess.Popen(
                [str(PYTHON), *args],
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        state[role] = {"pid": process.pid, "log": str(LOGS / f"{role}.log"), "status": "started"}
        STATE.write_text(json.dumps(state, indent=2) + "\n")
        print(f"{role} 已启动（PID {process.pid}）", flush=True)
    return state


def api_ready():
    try:
        response = httpx.get("http://127.0.0.1:8000/health/ready", timeout=2)
        return response.is_success and response.json().get("status") == "ready"
    except (httpx.HTTPError, ValueError):
        return False


def run():
    os.chdir(ROOT)
    if not PYTHON.exists():
        raise RuntimeError("请先执行 uv sync --locked --extra dev 安装项目依赖")
    settings = Settings()
    if settings.environment != "development":
        raise RuntimeError("此脚本仅用于 development 本机环境；部署请使用 Compose")
    media_tools(settings)
    dependencies(settings)
    # Use the same environment in API, Worker, Beat, and the readiness inspector.
    os.environ["TASK_MODE"] = "worker"
    print("检查并应用数据库迁移…", flush=True)
    with (LOGS / "migrate.log").open("ab") as log:
        subprocess.run(
            [str(PYTHON), "-m", "alembic", "upgrade", "head"],
            cwd=str(ROOT),
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            stdout=log,
            stderr=log,
            check=True,
        )
    state = start_apps()
    wait_until(api_ready, "API")
    from app.workers.celery_app import celery_app

    destination = "ai-interview@" + socket.gethostname()
    wait_until(
        lambda: bool(celery_app.control.inspect(destination=[destination], timeout=2).ping()), "Worker"
    )
    for role in ("api", "worker", "beat"):
        if not matches(state[role]["pid"], role):
            raise RuntimeError(f"{role} 已退出，请检查 {LOGS / (role + '.log')}")
    print("\n启动完成：MySQL / Redis / API / Worker / Beat", flush=True)
    print("接口文档：http://localhost:8000/docs", flush=True)
    print(f"日志目录：{LOGS}", flush=True)


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with (RUN / "start.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有启动命令正在执行，请稍候。", flush=True)
            return 1
        try:
            run()
        except RuntimeError as exc:
            print(str(exc), flush=True)
            return 1
        except (ValueError, OSError, subprocess.SubprocessError):
            # Do not echo Settings validation inputs, commands, or upstream errors:
            # they can include credentials. Details from subprocesses stay local.
            print(f"启动未完成，请检查本机依赖及 {LOGS} 中的日志。", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
