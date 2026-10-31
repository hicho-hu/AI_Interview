from datetime import timedelta

from sqlalchemy import select

from app.core.errors import require
from app.core.security import new_id, utcnow
from app.core.state import transition
from app.db.models import InterviewPlan, JobProfile, MediaAsset, ResumeAsset, Turn
from app.modules.identity.service import IdentityService
from app.modules.interview.service import InterviewService
from app.modules.shared import Service
from app.providers.ai import decode_audio
from app.schemas.contracts import Decision, Plan, SynthesizedAudio, Transcript


class EngineService(Service):
    def prepare(self, row):
        if row.status != "PREPARING":
            return {"status": row.status}
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        job = self.db.get(JobProfile, row.job_profile_id)
        resume = self.db.get(ResumeAsset, row.resume_asset_id) if row.resume_asset_id else None
        jd = self.rt.security.decrypt(job.jd_encrypted)

        def validate(plan):
            require(
                len(plan["questions"]) == row.config_json["question_count"],
                "AI_OUTPUT_INVALID",
                "面试计划题数不符",
                502,
            )
            require(
                all(q["jd_evidence"] in jd for q in plan["questions"]),
                "AI_OUTPUT_INVALID",
                "问题未提供真实 JD 证据",
                502,
            )
            require(
                len({q["text"] for q in plan["questions"]}) == len(plan["questions"]),
                "AI_OUTPUT_INVALID",
                "面试计划包含重复问题",
                502,
            )

        plan = self.rt.ai(
            self.db,
            self.uid,
            "plan",
            {
                "job_title": job.job_title,
                "jd_text": jd,
                "job_profile": self.decrypt_json(job.profile_encrypted),
                "resume": self.decrypt_json(resume.summary_encrypted, {}) if resume else {},
                "config": row.config_json,
            },
            Plan,
            row.id,
            request_id=self.rid,
            validate=validate,
        )
        self.db.add(
            InterviewPlan(
                interview_id=row.id, plan_encrypted=self.encrypt_json(plan), model_name=self.rt.model_name
            )
        )
        self.new_turn(row, plan["questions"][0], 0)
        transition(row, "READY")
        row.failure_code = None
        return {"status": row.status}

    def new_turn(self, row, question, main_index, kind="QUESTION", parent=None):
        row.current_turn_no += 1
        turn = Turn(
            interview_id=row.id,
            turn_no=row.current_turn_no,
            main_index=main_index,
            competency=question["competency"],
            question_encrypted=self.rt.security.encrypt(question["text"]),
            turn_type=kind,
            parent_turn_id=parent,
        )
        self.db.add(turn)
        self.db.flush()
        if row.mode != "TEXT":
            output = self.rt.ai(
                self.db,
                self.uid,
                "tts",
                {"text": question["text"], "language": row.config_json["language"]},
                SynthesizedAudio,
                row.id,
                turn.id,
                self.rid,
            )
            audio = decode_audio(output["audio_base64"])
            if audio:
                require(
                    len(audio) <= 10 * 1024 * 1024 and output.get("mime_type") == "audio/mpeg",
                    "AI_OUTPUT_INVALID",
                    "TTS 音频格式或大小无效",
                    502,
                )
                asset_id = new_id()
                key = f"{self.rt.settings.environment}/users/{self.uid}/tts/{asset_id}.mp3"
                self.rt.storage.put(key, audio, "audio/mpeg")
                asset = MediaAsset(
                    id=asset_id,
                    user_id=self.uid,
                    interview_id=row.id,
                    turn_id=turn.id,
                    media_type="TTS",
                    object_key=key,
                    filename=f"{asset_id}.mp3",
                    mime_type="audio/mpeg",
                    size_bytes=len(audio),
                    status="READY",
                    expires_at=utcnow() + timedelta(days=self.rt.settings.media_retention_days),
                )
                self.db.add(asset)
                self.db.flush()
                turn.question_asset_id = asset.id
        return turn

    def answer(self, row, turn, payload=None):
        if row.status not in ("IN_PROGRESS", "PAUSED") or turn.status == "COMPLETED":
            return {"status": row.status, "turn_id": turn.id}
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        if not turn.transcript_encrypted:
            asset_ids = (payload or {}).get("asset_ids") or [turn.answer_asset_id]
            transcripts = []
            for asset_id in asset_ids:
                transcripts.append(self.transcribe(row, turn, asset_id))
            text = "\n".join(t["text"] for t in transcripts)
            require(len(text) <= 20000, "AI_OUTPUT_INVALID", "回答转写超过限制", 502)
            turn.transcript_encrypted = self.rt.security.encrypt(text)
            confidence_values = [t["confidence"] for t in transcripts if t["confidence"] is not None]
            turn.confidence = min(confidence_values) if confidence_values else None
            from app.runtime import checkpoint_buffer

            checkpoints = checkpoint_buffer.get()
            if checkpoints is not None:
                checkpoints.append((turn.id, turn.transcript_encrypted, turn.confidence))
        return self.decide(row, turn)

    def transcribe(self, row, turn, asset_id):
        asset = self.db.get(MediaAsset, asset_id)
        from app.modules.media.service import aware

        require(
            asset and asset.status == "READY" and aware(asset.expires_at) > utcnow(),
            "MEDIA_INVALID_ASSET",
            "回答媒体已删除或过期",
            409,
        )
        transcript = self.rt.ai(
            self.db,
            self.uid,
            "asr",
            {
                "file_url": self.rt.storage.url(asset),
                "object_key": asset.object_key,
                "mime_type": asset.mime_type,
                "language": row.config_json["language"],
                "duration_ms": asset.duration_ms,
            },
            Transcript,
            row.id,
            turn.id,
            self.rid,
        )
        return transcript

    def decide(self, row, turn):
        text = self.rt.security.decrypt(turn.transcript_encrypted)
        orchestration = InterviewService(self.rt, self.db, self.uid, self.rid)
        turns = orchestration.turns(row)
        same_main = [t for t in turns if t.main_index == turn.main_index]
        follow_count = sum(t.turn_type == "FOLLOW_UP" for t in same_main)
        clarify_count = sum(t.turn_type == "CLARIFICATION" for t in same_main)
        config = row.config_json
        low_quality = not text.strip() or (turn.confidence is not None and turn.confidence < 0.6)
        if low_quality:
            decision = {
                "action": "CLARIFY",
                "question": "未能清楚识别刚才的回答，请重新说明或改用文字回答。",
                "reason": "ASR 置信度不足，此轮不计入能力评分",
                "answer_summary": "",
            }
        else:
            job = self.db.get(JobProfile, row.job_profile_id)
            decision = self.rt.ai(
                self.db,
                self.uid,
                "decision",
                {
                    "job_profile": self.decrypt_json(job.profile_encrypted),
                    "config": config,
                    "question": self.rt.security.decrypt(turn.question_encrypted),
                    "answer": text,
                    "follow_up_count": follow_count,
                    "recent_turns": [
                        {
                            "question": self.rt.security.decrypt(t.question_encrypted),
                            "answer_summary": self.decrypt_json(t.decision_encrypted, {}).get(
                                "answer_summary", ""
                            ),
                        }
                        for t in turns
                        if t.status == "COMPLETED"
                    ][-6:],
                    "covered_competencies": list({t.competency for t in turns if t.status == "COMPLETED"}),
                },
                Decision,
                row.id,
                turn.id,
                self.rid,
            )
        if decision["action"] == "FOLLOW_UP" and (
            not config["allow_follow_up"] or follow_count >= config["max_follow_ups"]
        ):
            decision["action"] = "NEXT_QUESTION"
        if decision["action"] == "CLARIFY" and clarify_count >= config["max_clarifications"]:
            decision["action"] = "NEXT_QUESTION"
        # LLM cannot unilaterally terminate a session before server-side limits.
        if decision["action"] == "FINISH":
            decision["action"] = "NEXT_QUESTION"
        turn.decision_encrypted = self.encrypt_json(decision)
        turn.status = "COMPLETED"
        if orchestration.elapsed(row) >= config["duration_minutes"] * 60:
            decision["action"] = "FINISH"
        if decision["action"] in ("FOLLOW_UP", "CLARIFY"):
            require(decision.get("question"), "AI_OUTPUT_INVALID", "追问或澄清缺少问题", 502)
            self.new_turn(
                row,
                {"text": decision["question"], "competency": turn.competency},
                turn.main_index,
                "FOLLOW_UP" if decision["action"] == "FOLLOW_UP" else "CLARIFICATION",
                turn.id,
            )
        elif decision["action"] == "FINISH" or turn.main_index + 1 >= config["question_count"]:
            decision["action"] = "FINISH"
            orchestration.schedule_report(row)
        else:
            plan = self.db.scalar(select(InterviewPlan).where(InterviewPlan.interview_id == row.id))
            question = self.decrypt_json(plan.plan_encrypted)["questions"][turn.main_index + 1]
            self.new_turn(row, question, turn.main_index + 1)
        turn.decision_encrypted = self.encrypt_json(decision)
        return {"decision": decision, "status": row.status, "current_turn_no": row.current_turn_no}
