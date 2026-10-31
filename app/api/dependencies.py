import time

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.errors import require
from app.modules.identity.service import IdentityService


def db_session(request: Request):
    rt = request.app.state.runtime
    with rt.db.session() as session:
        yield session
    if rt.settings.task_mode == "eager":
        rt.drain()


def current_user(
    request: Request, db: Session = Depends(db_session, scope="function"), authorization: str = Header("")
):
    require(authorization.startswith("Bearer "), "AUTH_UNAUTHORIZED", "请先登录", 401)
    svc = IdentityService(request.app.state.runtime, db)
    user, claims = svc.authenticate(authorization[7:])
    if user.status == "DELETING":
        require(
            request.method == "GET" and "/tasks/" in request.url.path,
            "AUTH_ACCOUNT_DELETING",
            "账号数据正在删除",
            403,
        )
    request.state.user_id, request.state.claims = user.id, claims
    rt = request.app.state.runtime
    require(
        rt.cache.hit(f"rate:user:{user.id}:{int(time.time() // 60)}", rt.settings.rate_limit_per_minute),
        "RATE_LIMITED",
        "请求过于频繁",
        429,
    )
    return user


def admin_user(request: Request, user=Depends(current_user)):
    require(
        user.id in request.app.state.runtime.settings.admin_user_ids.split(","),
        "AUTH_FORBIDDEN",
        "需要管理员权限",
        403,
    )
    return user


def service(cls, request, db, user):
    return cls(request.app.state.runtime, db, user.id, request.state.request_id)


def ok(request, data, code="OK"):
    return {"code": code, "message": "success", "data": data, "request_id": request.state.request_id}
