from sqlalchemy import select

from app.core.errors import require
from app.db.models import Interview, JobProfile, MediaAsset
from app.db.repository import owned
from app.modules.identity.service import IdentityService
from app.modules.shared import Service
from app.providers.storage import extract_resume
from app.schemas.contracts import JobStructure, ResumeStructure


class JobService(Service):
    def update(self, job_id, data):
        row = owned(self.db, JobProfile, job_id, self.uid)
        require(
            not self.db.scalar(select(Interview.id).where(Interview.job_profile_id == job_id)),
            "JOB_IN_USE",
            "已有面试使用此岗位，请创建新版本",
            409,
        )
        row.job_title, row.seniority = data.job_title, data.seniority
        row.jd_encrypted = self.rt.security.encrypt(data.jd_text)
        row.profile_encrypted, row.status = None, "PREPARING"
        row.task_id = self.task("PARSE_JOB", row.id).id
        return self.view(row)

    def create(self, data):
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        row = JobProfile(
            user_id=self.uid,
            job_title=data.job_title,
            seniority=data.seniority,
            jd_encrypted=self.rt.security.encrypt(data.jd_text),
        )
        self.db.add(row)
        self.db.flush()
        row.task_id = self.task("PARSE_JOB", row.id).id
        self.audit("JOB_CREATED", row.id)
        return self.view(row)

    def parse(self, row):
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        profile = self.rt.ai(
            self.db,
            self.uid,
            "job",
            {
                "job_title": row.job_title,
                "jd_text": self.rt.security.decrypt(row.jd_encrypted),
                "seniority": row.seniority,
            },
            JobStructure,
            request_id=self.rid,
        )
        row.profile_encrypted = self.encrypt_json(profile)
        row.status = "READY"
        return {"job_profile_id": row.id, "status": row.status}

    def view(self, row):
        return {
            "id": row.id,
            "job_title": row.job_title,
            "seniority": row.seniority,
            "jd_text": self.rt.security.decrypt(row.jd_encrypted),
            "status": row.status,
            "structure": self.decrypt_json(row.profile_encrypted),
            "task_id": row.task_id,
            "created_at": str(row.created_at),
        }

    def delete(self, job_id):
        row = owned(self.db, JobProfile, job_id, self.uid)
        used = self.db.scalar(select(Interview.id).where(Interview.job_profile_id == job_id))
        active = self.db.scalar(
            select(Interview.id).where(Interview.job_profile_id == job_id, Interview.deleted_at.is_(None))
        )
        require(not active, "JOB_IN_USE", "岗位已有面试记录，请先删除相关面试", 409)
        if used:
            row.job_title, row.jd_encrypted = "[已删除]", self.rt.security.encrypt("[已删除]")
            row.profile_encrypted, row.status = None, "DELETED"
        else:
            self.db.delete(row)
        self.audit("JOB_DELETED", job_id)
        return {"deleted": True}


class ResumeService(Service):
    def parse(self, row):
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        asset = owned(self.db, MediaAsset, row.asset_id, self.uid)
        text = extract_resume(self.rt.storage.read(asset.object_key), asset.mime_type)
        structure = self.rt.ai(
            self.db, self.uid, "resume", {"text": text}, ResumeStructure, request_id=self.rid
        )
        require(all(e in text for e in structure["evidence"]), "AI_OUTPUT_INVALID", "简历证据不在原文中", 502)
        row.text_encrypted = self.rt.security.encrypt(text)
        row.summary_encrypted = self.encrypt_json(structure)
        row.parse_status = "READY"
        return {"resume_id": row.id, "status": "READY"}

    def view(self, row):
        return {
            "id": row.id,
            "asset_id": row.asset_id,
            "parse_status": row.parse_status,
            "summary": self.decrypt_json(row.summary_encrypted),
            "task_id": row.task_id,
            "created_at": str(row.created_at),
        }
