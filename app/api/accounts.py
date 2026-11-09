from fastapi import APIRouter, Depends, Request

from app.api.dependencies import current_user, db_session, ok, service
from app.modules.accounts.service import AccountService
from app.schemas import accounts as schema
from app.schemas.contracts import Envelope

router = APIRouter(tags=["邮箱账号与资料"])


def ip(request):
    return request.client.host if request.client else "local"


@router.post("/auth/email-code", response_model=Envelope)
def email_code(data: schema.EmailCodeInput, request: Request, db=Depends(db_session, scope="function")):
    return ok(
        request,
        AccountService(request.app.state.runtime, db).send_code(data.email, data.purpose, ip(request)),
    )


@router.post("/auth/email-register", response_model=Envelope, status_code=201)
def register(data: schema.EmailRegister, request: Request, db=Depends(db_session, scope="function")):
    return ok(request, AccountService(request.app.state.runtime, db).register(data))


@router.post("/auth/email-login", response_model=Envelope)
def login(data: schema.EmailLogin, request: Request, db=Depends(db_session, scope="function")):
    return ok(request, AccountService(request.app.state.runtime, db).login(data, ip(request)))


@router.post("/auth/password-reset", response_model=Envelope)
def reset(data: schema.PasswordReset, request: Request, db=Depends(db_session, scope="function")):
    return ok(request, AccountService(request.app.state.runtime, db).reset_password(data))


@router.post("/users/me/email-code", response_model=Envelope)
def binding_code(
    data: schema.EmailInput,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(
        request, service(AccountService, request, db, user).send_code(data.email, "BIND_EMAIL", ip(request))
    )


@router.post("/users/me/email", response_model=Envelope)
def bind(
    data: schema.EmailBinding,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(AccountService, request, db, user).bind_email(data))


@router.post("/users/me/password", response_model=Envelope)
def password(
    data: schema.PasswordChange,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(AccountService, request, db, user).change_password(data))


@router.patch("/users/me/avatar", response_model=Envelope)
def avatar(
    data: schema.AvatarInput,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(
        request,
        service(AccountService, request, db, user).avatar(data.asset_id, data.system_avatar_key),
    )


@router.patch("/users/me/contact", response_model=Envelope)
def contact(
    data: schema.ContactInput,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    return ok(request, service(AccountService, request, db, user).contact(data.phone.strip()))
