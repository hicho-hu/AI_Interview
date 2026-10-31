from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api.dependencies import current_user, db_session, ok, service
from app.db.models import JobProfile, ResumeAsset
from app.db.repository import owned, page_query
from app.modules.job_profile.service import JobService, ResumeService
from app.modules.media.service import MediaService
from app.schemas.contracts import Envelope, JobInput, UploadInput

router = APIRouter(tags=["岗位与简历"])


@router.post("/job-profiles", response_model=Envelope, status_code=201)
def create_job(
    data: JobInput, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, service(JobService, request, db, user).create(data))


@router.get("/job-profiles", response_model=Envelope)
def jobs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    items, total = page_query(
        db,
        select(JobProfile)
        .where(JobProfile.user_id == user.id, JobProfile.status != "DELETED")
        .order_by(JobProfile.created_at.desc()),
        page,
        page_size,
    )
    svc = service(JobService, request, db, user)
    return ok(
        request, {"items": [svc.view(r) for r in items], "total": total, "page": page, "page_size": page_size}
    )


@router.get("/job-profiles/{job_id}", response_model=Envelope)
def job(job_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)):
    return ok(request, service(JobService, request, db, user).view(owned(db, JobProfile, job_id, user.id)))


@router.patch("/job-profiles/{job_id}", response_model=Envelope)
def patch_job(
    job_id: str,
    data: JobInput,
    request: Request,
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    svc = service(JobService, request, db, user)
    return ok(request, svc.update(job_id, data))


@router.delete("/job-profiles/{job_id}", response_model=Envelope)
def delete_job(
    job_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, service(JobService, request, db, user).delete(job_id))


@router.post("/resumes/upload-url", response_model=Envelope, status_code=201)
def resume_upload(
    data: UploadInput, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(request, service(MediaService, request, db, user).upload(data, resume=True))


@router.get("/resumes", response_model=Envelope)
def resumes(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(db_session, scope="function"),
    user=Depends(current_user),
):
    rows, total = page_query(
        db,
        select(ResumeAsset)
        .where(ResumeAsset.user_id == user.id, ResumeAsset.parse_status != "DELETED")
        .order_by(ResumeAsset.created_at.desc()),
        page,
        page_size,
    )
    svc = service(ResumeService, request, db, user)
    return ok(
        request, {"items": [svc.view(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    )


@router.get("/resumes/{resume_id}", response_model=Envelope)
def resume(
    resume_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    return ok(
        request, service(ResumeService, request, db, user).view(owned(db, ResumeAsset, resume_id, user.id))
    )


@router.delete("/resumes/{resume_id}", response_model=Envelope)
def delete_resume(
    resume_id: str, request: Request, db=Depends(db_session, scope="function"), user=Depends(current_user)
):
    row = owned(db, ResumeAsset, resume_id, user.id)
    row.parse_status, row.text_encrypted, row.summary_encrypted = "DELETED", None, None
    return ok(request, service(MediaService, request, db, user).delete(row.asset_id))
