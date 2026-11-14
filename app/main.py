import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException

from app.api import accounts, admin, billing, growth, identity, interviews, jobs, media, ops
from app.api.v1 import storage as cloud_storage
from app.core.errors import BusinessError
from app.core.security import new_id
from app.runtime import Runtime

logger = logging.getLogger("ai_interview")


def create_app(settings=None, runtime=None):
    rt = runtime or Runtime(settings)

    @asynccontextmanager
    async def lifespan(app):
        from app.workers.background import BackgroundTaskRunner

        # Schema changes are explicitly migrated through Alembic, including local development.
        with rt.db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if rt.settings.task_mode == "eager":
            rt.drain()
        runner = None
        if rt.settings.task_mode == "worker" and rt.settings.task_fallback_enabled:
            runner = BackgroundTaskRunner(rt)
            runner.start()
        try:
            yield
        finally:
            if runner:
                runner.stop()
            rt.db.engine.dispose()

    app = FastAPI(
        title="AI 模拟面试后端",
        version="1.0.0",
        lifespan=lifespan,
        description="岗位/简历/面试/语音/视频/报告/权益。默认开发环境使用明确标识的模拟 AI。",
    )
    app.state.runtime = rt
    app.add_middleware(
        CORSMiddleware,
        allow_origins=rt.settings.cors_origins.split(","),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    def error(request, status, code, message):
        headers = {"X-Request-ID": getattr(request.state, "request_id", new_id())}
        if status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=status,
            content={"code": code, "message": message, "data": None, "request_id": headers["X-Request-ID"]},
            headers=headers,
        )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", incoming) else new_id()
        started = time.monotonic()
        try:
            client = request.client.host if request.client else "unknown"
            if not rt.cache.hit(
                f"rate:ip:{client}:{int(time.time() // 60)}", rt.settings.rate_limit_per_minute * 4
            ):
                response = error(request, 429, "RATE_LIMITED", "请求过于频繁")
            else:
                response = await call_next(request)
        except BusinessError as exc:
            response = error(request, exc.status, exc.code, exc.message)
        except Exception:
            logger.error(json.dumps({"event": "request_failed", "request_id": request.state.request_id}))
            response = error(request, 500, "INTERNAL_ERROR", "服务内部错误")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            )
        )
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request, exc):
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = ", ".join(".".join(str(x) for x in e["loc"]) for e in exc.errors())
        return error(request, 422, "VALIDATION_ERROR", "参数校验失败：" + fields)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(IntegrityError)
    @app.exception_handler(StaleDataError)
    async def conflict_error(request, exc):
        return error(request, 409, "CONCURRENT_MODIFICATION", "资源已被其他请求更新，请重试相同幂等请求")

    @app.exception_handler(OperationalError)
    async def db_error(request, exc):
        return error(request, 503, "DATABASE_UNAVAILABLE", "数据库暂不可用，请稍后重试")

    @app.get("/health/live")
    def live():
        return {
            "status": "ok",
            "environment": rt.settings.environment,
            "provider_mode": rt.settings.provider_mode,
        }

    @app.get("/health/ready")
    def ready(request: Request):
        try:
            with rt.db.engine.connect() as connection:
                connection.execute(text("SELECT version_num FROM alembic_version"))
            if rt.cache.redis and (
                rt.settings.redis_required
                or (rt.settings.task_mode == "worker" and not rt.settings.task_fallback_enabled)
            ):
                rt.cache.redis.ping()
            if rt.settings.storage_backend == "oss":
                rt.storage.check_ready()
            if rt.settings.storage_backend == "s3":
                rt.storage.client.head_bucket(Bucket=rt.settings.s3_bucket)
        except Exception:
            return error(request, 503, "DEPENDENCY_UNAVAILABLE", "数据库迁移/Redis/对象存储未就绪")
        return {"status": "ready"}

    for router in (
        admin.router,
        accounts.router,
        growth.router,
        identity.router,
        jobs.router,
        interviews.router,
        media.router,
        billing.router,
        ops.router,
    ):
        app.include_router(router, prefix="/api/v1")
    app.include_router(cloud_storage.router, prefix="/api/v1")
    return app


app = create_app()
