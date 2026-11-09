from sqlalchemy import delete, or_, select, update

from app.core.security import new_id, utcnow
from app.db.entitlement_models import CreditBalance
from app.db.growth_models import EmailChallenge, UserAccount
from app.db.models import (
    Consent,
    Feedback,
    Idempotency,
    Interview,
    InterviewPlan,
    JobProfile,
    MediaAsset,
    Report,
    ResumeAsset,
    Task,
    Turn,
    User,
)
from app.modules.media.service import MediaService
from app.modules.shared import Service


class PrivacyService(Service):
    def request_deletion(self):
        user = self.db.get(User, self.uid)
        existing = self.db.scalar(
            select(Task)
            .where(Task.user_id == self.uid, Task.event_type == "DELETE_USER")
            .order_by(Task.created_at.desc())
        )
        if existing:
            return {"task_id": existing.id, "status": existing.status}
        user.status = "DELETING"
        task = self.task("DELETE_USER", user.id)
        self.audit("USER_DATA_DELETE_REQUESTED", user.id)
        return {"task_id": task.id, "status": "PENDING"}

    def erase_interview(self, row):
        row.deleted_at = utcnow()
        row.failure_code = None
        row.config_json = {}
        row.resume_asset_id = None
        for task in self.db.scalars(select(Task).where(Task.aggregate_id == row.id)):
            task.payload_encrypted = None
            task.result_encrypted = None
            if task.status in ("PENDING", "RUNNING", "RETRYING", "QUEUED"):
                task.status = "CANCELLED"
        turns = self.db.scalars(select(Turn).where(Turn.interview_id == row.id)).all()
        for turn in turns:
            turn.question_encrypted = self.rt.security.encrypt("[已删除]")
            turn.transcript_encrypted = None
            turn.decision_encrypted = None
            turn.status = "DELETED"
            for task in self.db.scalars(select(Task).where(Task.aggregate_id == turn.id)):
                task.payload_encrypted = None
                task.result_encrypted = None
                task.status = "CANCELLED"
        self.db.execute(delete(InterviewPlan).where(InterviewPlan.interview_id == row.id))
        self.db.execute(delete(Report).where(Report.interview_id == row.id))
        for asset in self.db.scalars(select(MediaAsset).where(MediaAsset.interview_id == row.id)):
            MediaService(self.rt, self.db, self.uid, self.rid).delete(asset.id)
        # Cached API responses can contain deleted content; expire them for this owner.
        self.db.execute(delete(Idempotency).where(Idempotency.user_id == self.uid))
        self.audit("INTERVIEW_DATA_DELETED", row.id)

    def erase_user(self, task_id):
        for asset in self.db.scalars(select(MediaAsset).where(MediaAsset.user_id == self.uid)):
            self.rt.storage.delete(asset.object_key)
            asset.status, asset.deleted_at = "DELETED", utcnow()
            asset.filename = "[deleted]"
            asset.checksum, asset.etag = None, None
        for row in self.db.scalars(select(Interview).where(Interview.user_id == self.uid)).all():
            self.erase_interview(row)
        for row in self.db.scalars(select(JobProfile).where(JobProfile.user_id == self.uid)):
            row.job_title = "[已删除]"
            row.jd_encrypted = self.rt.security.encrypt("[已删除]")
            row.profile_encrypted = None
            row.status = "DELETED"
        for row in self.db.scalars(select(ResumeAsset).where(ResumeAsset.user_id == self.uid)):
            row.text_encrypted, row.summary_encrypted, row.parse_status = None, None, "DELETED"
        for row in self.db.scalars(select(Feedback).where(Feedback.user_id == self.uid)):
            row.content_encrypted, row.reply_encrypted = self.rt.security.encrypt("[已删除]"), None
        for row in self.db.scalars(select(Consent).where(Consent.user_id == self.uid)):
            row.revoked_at = utcnow()
        for task in self.db.scalars(select(Task).where(Task.user_id == self.uid, Task.id != task_id)):
            task.payload_encrypted, task.result_encrypted = None, None
            if task.status in ("PENDING", "RUNNING", "RETRYING", "QUEUED"):
                task.status = "CANCELLED"
        self.db.execute(update(CreditBalance).where(CreditBalance.user_id == self.uid).values(remaining=0))
        account = self.db.scalar(select(UserAccount).where(UserAccount.user_id == self.uid))
        if account:
            conditions = [EmailChallenge.user_id == self.uid]
            if account.email_hash:
                conditions.append(EmailChallenge.email_hash == account.email_hash)
            self.db.execute(delete(EmailChallenge).where(or_(*conditions)))
            account.email_hash = None
            account.email_encrypted = account.phone_encrypted = account.password_hash = (
                account.avatar_asset_id
            ) = None
            account.auth_version += 1
        user = self.db.get(User, self.uid)
        user.nickname, user.openid_hash, user.status, user.credits = "[已删除]", new_id(), "DELETED", 0
        self.audit("USER_DATA_DELETED", user.id)
        return {"deleted": True}
