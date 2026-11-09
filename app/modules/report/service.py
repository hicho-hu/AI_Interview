from sqlalchemy import func, select

from app.core.errors import require
from app.core.state import transition
from app.db.models import JobProfile, Report
from app.modules.identity.service import IdentityService
from app.modules.shared import Service
from app.providers.ai import DIMENSIONS
from app.schemas.contracts import ReportContent


class ReportService(Service):
    def generate(self, row, payload):
        if row.status != "FINISHING":
            return {"status": row.status}
        IdentityService(self.rt, self.db, self.uid).require_consents("PRIVACY", "TERMS", "AI_PROCESSING")
        job = self.db.get(JobProfile, row.job_profile_id)
        snapshot = payload["turns"]
        # Low confidence is an audio issue, never an ability score.
        usable = [t for t in snapshot if (t["confidence"] is None or t["confidence"] >= 0.6) and t["answer"]]

        def validate(data, segment=None):
            require(
                set(d["name"] for d in data["dimensions"]) == set(DIMENSIONS),
                "AI_OUTPUT_INVALID",
                "评分维度缺失或重复",
                502,
            )
            mapping = {t["turn_id"]: t["answer"] for t in (usable if segment is None else segment)}
            require(
                {r["turn_id"] for r in data["reviews"]} == set(mapping)
                and len(data["reviews"]) == len(mapping),
                "AI_OUTPUT_INVALID",
                "逐题点评不完整或重复",
                502,
            )
            for review in data["reviews"]:
                require(
                    all(e and e in mapping[review["turn_id"]] for e in review["evidence"]),
                    "AI_OUTPUT_INVALID",
                    "逐题证据不在回答中",
                    502,
                )
            for dimension in data["dimensions"]:
                require(
                    all(e and any(e in answer for answer in mapping.values()) for e in dimension["evidence"]),
                    "AI_OUTPUT_INVALID",
                    "维度证据不在回答中",
                    502,
                )
                if not dimension["evidence"]:
                    require(
                        dimension["score"] is None and "无法判断" in dimension["feedback"],
                        "AI_OUTPUT_INVALID",
                        "缺少证据时必须标记无法判断",
                        502,
                    )
                else:
                    require(
                        dimension["score"] is not None,
                        "AI_OUTPUT_INVALID",
                        "已有维度评价证据时须对本次表现评分，不完整或薄弱应在分数和反馈中体现",
                        502,
                    )
            scores = [d["score"] for d in data["dimensions"] if d["score"] is not None]
            expected = round(sum(scores) / len(scores), 1) if scores else None
            # Arithmetic is deterministic; derive the total from validated dimensions
            # rather than rejecting otherwise valid feedback for a model math error.
            data["overall_score"] = expected

        segments, current, size = [], [], 0
        for turn in usable:
            weight = len(turn["question"]) + len(turn["answer"])
            if current and size + weight > 24000:
                segments.append(current)
                current, size = [], 0
            current.append(turn)
            size += weight
        segments.append(current)  # Empty sessions still produce an explicit unassessable report.
        parts = []
        for segment in segments:
            part = self.rt.ai(
                self.db,
                self.uid,
                "report",
                {
                    "job_title": job.job_title,
                    "job_profile": self.decrypt_json(job.profile_encrypted),
                    "config": row.config_json,
                    "turns": segment,
                },
                ReportContent,
                row.id,
                request_id=self.rid,
                validate=lambda result, s=segment: validate(result, s),
            )
            parts.append(part)
        data = parts[0]
        if len(parts) > 1:
            data = {**data, "reviews": [r for p in parts for r in p["reviews"]]}
            dimensions = []
            for name in DIMENSIONS:
                values = [
                    (next(d for d in p["dimensions"] if d["name"] == name), len(s))
                    for p, s in zip(parts, segments)
                ]
                scored = [(d["score"], count) for d, count in values if d["score"] is not None]
                score = (
                    round(sum(v * n for v, n in scored) / sum(n for _, n in scored), 1) if scored else None
                )
                dimensions.append(
                    {
                        "name": name,
                        "score": score,
                        "feedback": "\n".join(d["feedback"] for d, _ in values),
                        "evidence": list(dict.fromkeys(e for d, _ in values for e in d["evidence"]))[:10],
                    }
                )
            data["dimensions"] = dimensions
            scores = [d["score"] for d in dimensions if d["score"] is not None]
            data["overall_score"] = round(sum(scores) / len(scores), 1) if scores else None
            for key in ("strengths", "gaps", "recommendations", "practice_questions"):
                data[key] = list(dict.fromkeys(v for p in parts for v in p[key]))[:10]
            data["job_match_feedback"] = "\n".join(p["job_match_feedback"] for p in parts)
            validate(data)
        data["disclaimer"] = "评分仅为训练参考，不代表招聘方评价或录用概率。"
        data["unassessable_turn_ids"] = [t["turn_id"] for t in snapshot if t not in usable]
        data["original_turns"] = snapshot
        version = (
            self.db.scalar(select(func.max(Report.report_version)).where(Report.interview_id == row.id)) or 0
        ) + 1
        report = Report(
            interview_id=row.id,
            report_version=version,
            overall_score=data["overall_score"],
            content_encrypted=self.encrypt_json(data),
            model_name=self.rt.model_name,
        )
        self.db.add(report)
        transition(row, "COMPLETED")
        row.failure_code = None
        self.db.flush()
        return {"report_id": report.id, "version": version, "status": "COMPLETED"}
