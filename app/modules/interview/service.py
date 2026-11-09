from sqlalchemy import select

from app.core.errors import require
from app.core.security import utcnow
from app.core.state import transition
from app.db.models import Interview, InterviewPlan, JobProfile, MediaAsset, Report, ResumeAsset, Task, Turn
from app.db.repository import owned
from app.modules.billing.service import BillingService
from app.modules.identity.service import IdentityService
from app.modules.media.service import MediaService, aware
from app.modules.shared import Service


class InterviewService(Service):
    def get(self, interview_id):
        row = owned(self.db, Interview, interview_id, self.uid)
        require(not row.deleted_at, "RESOURCE_NOT_FOUND", "面试不存在", 404)
        return row

    def create(self, data, key):
        def create_once():
            IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
            # Lock the owner before inserting FK children, so concurrent MySQL
            # reservations cannot deadlock while upgrading shared user locks.
            BillingService(self.rt, self.db, self.uid).lock_user()
            job = owned(self.db, JobProfile, data.job_profile_id, self.uid)
            require(job.status == "READY", "JOB_NOT_READY", "岗位解析未完成", 409)
            if data.resume_asset_id:
                resume = owned(self.db, ResumeAsset, data.resume_asset_id, self.uid)
                asset = owned(self.db, MediaAsset, resume.asset_id, self.uid)
                require(
                    resume.parse_status == "READY"
                    and asset.status == "READY"
                    and aware(asset.expires_at) > utcnow(),
                    "RESUME_NOT_READY",
                    "简历未就绪或已过期",
                    409,
                )
            row = Interview(
                user_id=self.uid,
                job_profile_id=data.job_profile_id,
                resume_asset_id=data.resume_asset_id,
                mode=data.config.mode,
                config_json=data.config.model_dump(),
            )
            self.db.add(row)
            self.db.flush()
            BillingService(self.rt, self.db, self.uid).reserve(row.id)
            row.prepare_task_id = self.task("PREPARE_INTERVIEW", row.id).id
            self.audit("INTERVIEW_CREATED", row.id)
            return {"interview_id": row.id, "status": row.status, "task_id": row.prepare_task_id}

        return self.idem("interview:create", key, data.model_dump(), create_once)

    def current(self, row):
        return self.db.scalar(
            select(Turn).where(Turn.interview_id == row.id, Turn.turn_no == row.current_turn_no)
        )

    def turns(self, row):
        return self.db.scalars(select(Turn).where(Turn.interview_id == row.id).order_by(Turn.turn_no)).all()

    def elapsed(self, row):
        if not row.started_at:
            return 0
        end = row.ended_at or row.paused_at or utcnow()
        return max(0, (aware(end) - aware(row.started_at)).total_seconds() - row.paused_seconds)

    def view(self, row):
        self.rt.cache.put(
            f"interview:state:{row.id}",
            self.encrypt_json(
                {"status": row.status, "current_turn": row.current_turn_no, "version": row.version}
            ),
            900,
        )
        turn = self.current(row)
        report = self.db.scalar(
            select(Report)
            .where(Report.interview_id == row.id)
            .order_by(Report.report_version.desc())
            .limit(1)
        )
        plan = self.db.scalar(select(InterviewPlan).where(InterviewPlan.interview_id == row.id))
        job = self.db.get(JobProfile, row.job_profile_id)
        return {
            "id": row.id,
            "job_profile_id": row.job_profile_id,
            "job_title": job.job_title,
            "resume_asset_id": row.resume_asset_id,
            "status": row.status,
            "mode": row.mode,
            "config": row.config_json,
            "version": row.version,
            "current_turn_no": row.current_turn_no,
            "elapsed_seconds": self.elapsed(row),
            "created_at": str(row.created_at),
            "started_at": str(row.started_at) if row.started_at else None,
            "ended_at": str(row.ended_at) if row.ended_at else None,
            "current_question": self.turn_view(turn) if turn else None,
            "plan": self.decrypt_json(plan.plan_encrypted) if plan else None,
            "overall_score": report.overall_score if report else None,
            "prepare_task_id": row.prepare_task_id,
            "report_task_id": row.report_task_id,
            "failure_code": row.failure_code,
            "conversation_features": self.conversation_features(row),
        }

    def turn_view(self, turn):
        media = MediaService(self.rt, self.db, self.uid)
        question_asset = self.db.get(MediaAsset, turn.question_asset_id) if turn.question_asset_id else None
        answer_asset = self.db.get(MediaAsset, turn.answer_asset_id) if turn.answer_asset_id else None
        return {
            "id": turn.id,
            "turn_no": turn.turn_no,
            "parent_turn_id": turn.parent_turn_id,
            "turn_type": turn.turn_type,
            "main_index": turn.main_index,
            "competency": turn.competency,
            "question": self.rt.security.decrypt(turn.question_encrypted),
            "status": turn.status,
            "transcript": self.rt.security.decrypt(turn.transcript_encrypted),
            "asr_confidence": turn.confidence,
            "decision": self.decrypt_json(turn.decision_encrypted),
            "task_id": turn.task_id,
            "question_audio": media.view(question_asset) if question_asset else None,
            "answer_media": media.view(answer_asset) if answer_asset else None,
        }

    def conversation_features(self, row):
        from app.providers.qwen_realtime import endpoint

        return {
            "streaming_answers": row.mode != "TEXT",
            "live_transcription": bool(endpoint(self.rt.settings)),
        }

    def start(self, row):
        if row.status == "READY":
            transition(row, "IN_PROGRESS")
            row.started_at = utcnow()
            BillingService(self.rt, self.db, self.uid).settle(row.id, True)
            self.audit("INTERVIEW_STARTED", row.id)
        require(row.status == "IN_PROGRESS", "INTERVIEW_INVALID_STATE", "面试尚未就绪或已结束", 409)
        return self.view(row)

    def pause(self, row):
        if row.status == "PAUSED":
            return self.view(row)
        transition(row, "PAUSED")
        row.paused_at = utcnow()
        self.audit("INTERVIEW_PAUSED", row.id)
        return self.view(row)

    def resume(self, row):
        if row.status == "IN_PROGRESS":
            return self.view(row)
        transition(row, "IN_PROGRESS")
        row.paused_seconds += int((utcnow() - aware(row.paused_at)).total_seconds())
        row.paused_at = None
        self.audit("INTERVIEW_RESUMED", row.id)
        return self.view(row)

    def submit(self, row, turn_id, data, key):
        def submit_once():
            require(row.status == "IN_PROGRESS", "INTERVIEW_INVALID_STATE", "面试未进行中", 409)
            require(
                self.elapsed(row) < row.config_json["duration_minutes"] * 60,
                "INTERVIEW_TIME_EXPIRED",
                "面试时长已到，请结束并生成报告",
                409,
            )
            turn = self.current(row)
            require(turn and turn.id == turn_id, "INTERVIEW_NOT_CURRENT_TURN", "不是当前待回答轮次", 409)
            require(turn.status == "WAITING_ANSWER", "INTERVIEW_ALREADY_SUBMITTED", "回答已提交", 409)
            IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
            if data.asset_id or data.asset_ids:
                assets = [
                    owned(self.db, MediaAsset, aid, self.uid) for aid in (data.asset_ids or [data.asset_id])
                ]
                for asset in assets:
                    require(
                        asset.interview_id == row.id
                        and asset.turn_id == turn.id
                        and asset.media_type in ("AUDIO", "VIDEO")
                        and asset.status == "READY"
                        and aware(asset.expires_at) > utcnow(),
                        "MEDIA_INVALID_ASSET",
                        "媒体归属、用途或状态无效",
                        409,
                    )
                asset = assets[0]
                require(
                    not data.client_duration_ms
                    or abs(data.client_duration_ms - sum(a.duration_ms for a in assets)) <= 1500,
                    "MEDIA_DURATION_MISMATCH",
                    "提交时长不一致",
                )
                turn.answer_asset_id = asset.id
                if len(assets) == 1 and data.live_transcript_id:
                    from app.modules.interview.transcription import verified_transcript

                    text = verified_transcript(self.rt, self.uid, turn.id, data.live_transcript_id, asset)
                    if text:
                        turn.transcript_encrypted = self.rt.security.encrypt(text)
                        turn.confidence = None
                require(
                    sum(a.duration_ms for a in assets) <= 180000, "MEDIA_TOO_LONG", "单次回答累计最长 3 分钟"
                )
            else:
                turn.transcript_encrypted = self.rt.security.encrypt(data.text)
                turn.confidence = 1.0
            turn.status = "SUBMITTED"
            task = self.task(
                "PROCESS_ANSWER",
                turn.id,
                {"asset_ids": data.asset_ids or ([data.asset_id] if data.asset_id else [])},
            )
            turn.task_id = task.id
            row.updated_at = utcnow()  # force version CAS for concurrent submit/finish
            self.audit("ANSWER_SUBMITTED", turn.id)
            return {"task_id": task.id, "processing_status": "SUBMITTED"}

        return self.idem(f"answer:{turn_id}", key, data.model_dump(), submit_once)

    def finish(self, row, reason, key):
        return self.idem(f"finish:{row.id}", key, {"reason": reason}, lambda: self.schedule_report(row))

    def schedule_report(self, row):
        if row.status in ("FINISHING", "COMPLETED", "REPORT_FAILED") and row.report_task_id:
            return {"task_id": row.report_task_id, "report_status": row.status}
        require(row.status in ("IN_PROGRESS", "PAUSED"), "INTERVIEW_INVALID_STATE", "面试不可结束", 409)
        turns = self.turns(row)
        require(
            not any(t.status in ("SUBMITTED", "TRANSCRIBING", "ANALYZING", "FAILED") for t in turns),
            "INTERVIEW_ANSWER_PROCESSING",
            "回答正在处理或需重试，请稍后结束",
            409,
        )
        if row.paused_at:
            row.paused_seconds += int((utcnow() - aware(row.paused_at)).total_seconds())
            row.paused_at = None
        transition(row, "FINISHING")
        row.ended_at = utcnow()
        snapshot = self.snapshot(turns)
        row.report_task_id = self.task("GENERATE_REPORT", row.id, {"turns": snapshot}).id
        self.audit("INTERVIEW_FINISHED", row.id)
        return {"task_id": row.report_task_id, "report_status": row.status}

    def snapshot(self, turns):
        return [
            {
                "turn_id": t.id,
                "question": self.rt.security.decrypt(t.question_encrypted),
                "answer": self.rt.security.decrypt(t.transcript_encrypted),
                "competency": t.competency,
                "confidence": t.confidence,
            }
            for t in turns
            if t.status == "COMPLETED"
        ]

    def cancel(self, row):
        require(
            row.status not in ("FINISHING", "COMPLETED"), "INTERVIEW_INVALID_STATE", "已结束面试不可取消", 409
        )
        if row.status != "CANCELLED":
            transition(row, "CANCELLED")
            row.ended_at = utcnow()
            BillingService(self.rt, self.db, self.uid).settle(row.id, False)
            self.audit("INTERVIEW_CANCELLED", row.id)
        return {"status": row.status}

    def regenerate(self, row, key):
        def regenerate_once():
            require(row.status == "COMPLETED", "INTERVIEW_INVALID_STATE", "面试报告未完成", 409)
            transition(row, "FINISHING")
            task = self.task("GENERATE_REPORT", row.id, {"turns": self.snapshot(self.turns(row))})
            row.report_task_id = task.id
            self.audit("REPORT_REGENERATED", row.id)
            return {"task_id": task.id, "report_status": row.status}

        return self.idem(f"regenerate:{row.id}", key, {}, regenerate_once)

    def report(self, row, version=None):
        task = (
            self.db.scalar(
                select(Task).where(
                    Task.id == row.report_task_id,
                    Task.user_id == self.uid,
                    Task.event_type == "GENERATE_REPORT",
                    Task.aggregate_id == row.id,
                )
            )
            if row.report_task_id
            else None
        )
        progress = {
            "status": row.status,
            "task_id": row.report_task_id,
            "processing_status": task.status if task else None,
            "retry_count": task.retry_count if task else 0,
            "last_error_code": task.error_code if task else None,
            "failure_code": row.failure_code,
            "retryable": bool(task and task.status == "FAILED" and row.status == "REPORT_FAILED"),
        }
        stmt = select(Report).where(Report.interview_id == row.id)
        if version:
            stmt = stmt.where(Report.report_version == version)
        report = self.db.scalar(stmt.order_by(Report.report_version.desc()).limit(1))
        if not report:
            return {**progress, "report": None}
        result = self.decrypt_json(report.content_encrypted)
        result.update(
            {
                "id": report.id,
                "version": report.report_version,
                "model": report.model_name,
                "prompt_version": report.prompt_version,
                "created_at": str(report.created_at),
                "is_mock": report.model_name == "mock",
            }
        )
        return {**progress, "report": result}
