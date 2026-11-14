"""Admin workflows reuse business services; all writes are audited and idempotent."""

import hashlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.core.errors import require
from app.core.security import new_id, utcnow
from app.db.admin_models import (
    AdminIdempotency,
    AdminSession,
    AdminUser,
    PaymentConfig,
    Product,
    QualityReview,
    Reconciliation,
    ReconciliationItem,
    RefundRequest,
)
from app.db.admin_repository import fields, interview_list, listing, mask
from app.db.models import AICallLog, Consent, Interview, JobProfile, MediaAsset, Order, Report, Usage, User
from app.modules.admin.auth import AdminAuth, create_admin
from app.modules.admin.security import permits
from app.modules.billing.service import BillingService, credit_balances
from app.modules.interview.service import InterviewService
from app.modules.shared import Service, dump

PRODUCT_FIELDS = "id code version name kind amount_minor original_minor credits interview_credits validity_days currency description status published_at created_at"
ORDER_FIELDS = "id user_id product_code amount_minor credits currency status paid_at refunded_at provider_trade_no created_at"
QUALITY_FIELDS = "id interview_id assignee_id created_by reason status tags conclusion created_at"


class AdminService(Service):
    def __init__(self, runtime, db, admin, request=None):
        super().__init__(runtime, db, admin.id, request.state.request_id if request else "cli")
        self.admin, self.request = admin, request
        self.auth = AdminAuth(runtime, db, request)

    def audit(self, action, resource_id="", reason=""):
        self.auth.audit(self.admin.id, action, resource_id, reason)

    def need(self, permission):
        require(permits(self.admin, permission), "ADMIN_FORBIDDEN", "当前角色没有该操作权限", 403)

    def get(self, model, entity_id):
        row = self.db.get(model, entity_id)
        require(row, "RESOURCE_NOT_FOUND", "资源不存在", 404)
        return row

    def idem(self, scope, key, payload, operation):
        require(key and 1 <= len(key) <= 120, "IDEMPOTENCY_REQUIRED", "请提供幂等键")
        digest = hashlib.sha256(dump(payload).encode()).hexdigest()
        # Serialize a given administrator's writes to cover concurrent duplicate requests.
        self.db.scalar(select(AdminUser).where(AdminUser.id == self.admin.id).with_for_update())
        row = self.db.scalar(
            select(AdminIdempotency).where(
                AdminIdempotency.admin_id == self.admin.id,
                AdminIdempotency.scope == scope,
                AdminIdempotency.key == key,
            )
        )
        if row:
            require(row.request_hash == digest, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", 409)
            return self.decrypt_json(row.response_encrypted)
        result = operation()
        self.db.add(
            AdminIdempotency(
                admin_id=self.admin.id,
                scope=scope,
                key=key,
                request_hash=digest,
                response_encrypted=self.encrypt_json(result),
            )
        )
        self.db.flush()
        return result

    def user_detail(self, uid):
        row = self.get(User, uid)
        self.audit("USER_VIEW", uid)
        consents = self.db.scalars(select(Consent).where(Consent.user_id == uid)).all()
        return {
            **fields(row, "id status credits created_at"),
            **credit_balances(self.db, row),
            "nickname": mask(row.nickname),
            "consents": [fields(c, "id consent_type policy_version revoked_at created_at") for c in consents],
            "interviews": interview_list(self.db, "", None, None, 1, 10, uid),
            "usage": listing(
                self.db,
                Usage,
                lambda r: {
                    **fields(r, "id usage_type quantity amount_minor status created_at"),
                    "credit_allocations": BillingService(self.rt, self.db, uid).allocations(r),
                },
                conditions=(Usage.user_id == uid,),
            ),
        }

    def user_status(self, uid, data):
        row = self.get(User, uid)
        require(row.status in ("ACTIVE", "FROZEN"), "USER_INVALID_STATE", "账号不可变更", 409)
        row.status = data.status
        self.audit("USER_" + data.status, uid, data.reason)
        return {"id": uid, "status": row.status}

    def grant(self, uid, data):
        billing = BillingService(self.rt, self.db, uid)
        user = billing.lock_user()
        require(user and user.status == "ACTIVE", "USER_INVALID_STATE", "仅能给正常账号补发权益", 409)
        billing.change_balance(data.mode, data.quantity)
        entry = billing.ledger(
            "ADMIN_GRANT", {data.mode: data.quantity}, "GRANTED", "admin-grant:" + new_id()
        )
        self.audit("ENTITLEMENT_GRANTED", uid, data.reason)
        if data.ticket:
            self.audit("ENTITLEMENT_TICKET", entry.id, data.ticket)
        self.db.refresh(user)
        return {
            "id": entry.id,
            "user_id": uid,
            "quantity": data.quantity,
            "mode": data.mode,
            **credit_balances(self.db, user),
            "status": entry.status,
        }

    def interview(self, iid):
        row = self.get(Interview, iid)
        owner = self.get(User, row.user_id)
        require(
            not row.deleted_at and owner.status not in ("DELETING", "DELETED"),
            "RESOURCE_NOT_FOUND",
            "资源不存在",
            404,
        )
        return row

    def interview_detail(self, iid):
        row = self.interview(iid)
        report = self.db.scalar(
            select(Report).where(Report.interview_id == iid).order_by(Report.report_version.desc()).limit(1)
        )
        assets = self.db.scalars(
            select(MediaAsset).where(MediaAsset.interview_id == iid, MediaAsset.deleted_at.is_(None))
        ).all()
        calls = self.db.scalars(
            select(AICallLog).where(AICallLog.interview_id == iid).order_by(AICallLog.created_at).limit(200)
        ).all()
        self.audit("INTERVIEW_METADATA_VIEW", iid)
        return {
            **fields(
                row, "id user_id mode status current_turn_no started_at ended_at created_at failure_code"
            ),
            "job_title": self.get(JobProfile, row.job_profile_id).job_title,
            "config": row.config_json,
            "report": fields(report, "id overall_score report_version model_name") if report else None,
            "media": [fields(m, "id media_type turn_id status duration_ms expires_at") for m in assets],
            "calls": [
                fields(
                    c,
                    "id provider model call_type input_tokens output_tokens latency_ms cost_microunits status",
                )
                for c in calls
            ],
        }

    def review_scope(self, row):
        if self.admin.role != "SUPER_ADMIN":
            assigned = self.db.scalar(
                select(QualityReview.id).where(
                    QualityReview.interview_id == row.id,
                    QualityReview.assignee_id == self.admin.id,
                    QualityReview.status.in_(["PENDING", "IN_REVIEW", "NEEDS_FIX"]),
                )
            )
            require(assigned, "REVIEW_SCOPE_DENIED", "该面试未分派给您或任务已关闭", 403)
        require(
            row.created_at + timedelta(days=self.rt.settings.media_retention_days) > utcnow(),
            "REVIEW_EXPIRED",
            "面试内容已超过审阅保留期限",
            410,
        )

    def content(self, iid, reason):
        row = self.interview(iid)
        self.review_scope(row)
        interview = InterviewService(self.rt, self.db, row.user_id, self.rid)
        turns = interview.turns(row)
        report = self.db.scalar(
            select(Report).where(Report.interview_id == iid).order_by(Report.report_version.desc()).limit(1)
        )
        self.audit("INTERVIEW_CONTENT_REVIEW", iid, reason)
        return {
            "turns": [
                {
                    **fields(t, "id turn_no turn_type competency status created_at"),
                    "question": self.rt.security.decrypt(t.question_encrypted),
                    "answer": self.rt.security.decrypt(t.transcript_encrypted),
                    "decision": self.decrypt_json(t.decision_encrypted),
                }
                for t in turns
            ],
            "report": self.decrypt_json(report.content_encrypted) if report else None,
        }

    def playback(self, asset_id, reason):
        asset = self.get(MediaAsset, asset_id)
        require(
            asset.media_type in ("AUDIO", "VIDEO", "SESSION_AUDIO", "SESSION_VIDEO"),
            "MEDIA_REVIEW_DENIED",
            "仅支持面试音视频",
            403,
        )
        row = self.interview(asset.interview_id)
        require(asset.user_id == row.user_id, "MEDIA_REVIEW_DENIED", "媒体归属不符", 403)
        self.review_scope(row)
        require(
            asset.status == "READY" and not asset.deleted_at and asset.expires_at > utcnow(),
            "MEDIA_EXPIRED",
            "媒体不可用或已过期",
            410,
        )
        seconds = min(120, int((asset.expires_at - utcnow()).total_seconds()))
        require(seconds > 0, "MEDIA_EXPIRED", "媒体已过期", 410)
        self.audit("MEDIA_PLAYBACK_GRANTED", asset_id, reason)
        if self.rt.settings.storage_backend == "s3":
            url = self.rt.storage.signer.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.rt.settings.s3_bucket, "Key": asset.object_key},
                ExpiresIn=seconds,
            )
        elif self.rt.settings.storage_backend == "oss" and self.rt.storage.cloud_key(asset.object_key):
            url = self.rt.storage.url(asset, seconds=seconds)
        else:
            expires = int(utcnow().timestamp()) + seconds
            signature = self.rt.security.sign_object(asset.id, "download", expires)
            url = (
                f"{self.rt.settings.public_base_url}/api/v1/local-objects/{asset.id}"
                f"?purpose=download&expires={expires}&signature={signature}"
            )
        return {"url": url, "expires_in": seconds, "media_type": asset.media_type}

    def retry_report(self, iid, reason, key):
        row = self.interview(iid)
        require(
            row.status in ("REPORT_FAILED", "COMPLETED"),
            "INTERVIEW_INVALID_STATE",
            "当前状态不可重试报告",
            409,
        )
        self.audit("REPORT_RETRY", iid, reason)
        svc = InterviewService(self.rt, self.db, row.user_id, self.rid)
        if row.status == "REPORT_FAILED":
            from app.core.state import transition

            transition(row, "FINISHING")
            task = svc.task("GENERATE_REPORT", row.id, {"turns": svc.snapshot(svc.turns(row))})
            row.report_task_id = task.id
            return {"task_id": task.id}
        return svc.regenerate(row, key)

    def product(self, data):
        version = (
            self.db.scalar(select(func.max(Product.version)).where(Product.code == data.code)) or 0
        ) + 1
        payload = data.model_dump(exclude={"reason"})
        row = Product(**payload, version=version, created_by=self.admin.id)
        self.db.add(row)
        self.db.flush()
        self.audit("PRODUCT_DRAFT_CREATED", row.id, data.reason)
        return fields(row, PRODUCT_FIELDS)

    def update_product(self, pid, data):
        row = self.get(Product, pid)
        require(row.status in ("DRAFT", "IN_REVIEW"), "PRODUCT_INVALID_STATE", "仅未上线商品可直接编辑", 409)
        require(data.code == row.code, "PRODUCT_CODE_IMMUTABLE", "商品 SKU 不可修改", 409)
        payload = data.model_dump(exclude={"reason", "code"})
        for name, value in payload.items():
            setattr(row, name, value)
        self.audit("PRODUCT_DRAFT_UPDATED", row.id, data.reason)
        return fields(row, PRODUCT_FIELDS)

    def publish(self, pid, reason):
        row = self.get(Product, pid)
        # Lock a SKU's versions in the same order, so concurrent publishes cannot
        # leave multiple versions active or retire a newer version accidentally.
        versions = self.db.scalars(
            select(Product)
            .where(Product.code == row.code)
            .order_by(Product.version)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        require(row.status in ("DRAFT", "IN_REVIEW"), "PRODUCT_INVALID_STATE", "仅未发布商品可发布", 409)
        newest = max((p.version for p in versions if p.status == "PUBLISHED"), default=0)
        require(newest < row.version, "PRODUCT_STALE_VERSION", "已有更高版本发布", 409)
        for product in versions:
            if product.status == "PUBLISHED":
                product.status = "RETIRED"
        row.status, row.published_at = "PUBLISHED", utcnow()
        self.audit("PRODUCT_PUBLISHED", row.id, reason)
        return fields(row, PRODUCT_FIELDS)

    def offline(self, pid, reason):
        row = self.get(Product, pid)
        require(row.status == "PUBLISHED", "PRODUCT_INVALID_STATE", "仅已上线商品可下线", 409)
        row.status = "RETIRED"
        self.audit("PRODUCT_OFFLINED", row.id, reason)
        return fields(row, PRODUCT_FIELDS)

    def payment(self, channel, data):
        require(channel in ("WECHAT", "ALIPAY"), "INVALID_CHANNEL", "渠道不存在", 404)
        require(
            not data.enabled,
            "PAYMENT_NOT_CONFIGURED",
            "真实支付适配器未配置，当前仅可保存关闭状态的配置",
            409,
        )
        config = self.db.scalar(
            select(PaymentConfig)
            .where(PaymentConfig.channel == channel, PaymentConfig.environment == data.environment)
            .with_for_update()
        )
        if not config:
            config = PaymentConfig(channel=channel, environment=data.environment, version=0)
            self.db.add(config)
        config.config_encrypted = self.encrypt_json(data.model_dump(exclude={"reason"}))
        config.enabled, config.version = data.enabled, config.version + 1
        self.db.flush()
        self.audit("PAYMENT_CONFIG_SAVED", config.id, data.reason)
        return fields(config, "id channel environment enabled version")

    def payments(self):
        rows = self.db.scalars(select(PaymentConfig)).all()
        return {
            "items": [
                {
                    **fields(r, "id channel environment enabled version"),
                    **{
                        k: (mask(v) if k in ("merchant_id", "app_id") else v)
                        for k, v in self.decrypt_json(r.config_encrypted).items()
                        if k != "enabled"
                    },
                }
                for r in rows
            ],
            "adapter_ready": False,
        }

    def refund(self, data):
        order = self.db.scalar(select(Order).where(Order.id == data.order_id).with_for_update())
        require(order, "RESOURCE_NOT_FOUND", "订单不存在", 404)
        require(order.status == "PAID", "REFUND_INVALID_STATE", "仅已支付订单可退款", 409)
        require(
            (order.provider_trade_no or "").startswith("dev:")
            and self.rt.settings.mock_payment_enabled,
            "REFUND_ADAPTER_NOT_CONFIGURED",
            "真实渠道退款适配器尚未配置",
            503,
        )
        require(
            not self.db.scalar(
                select(RefundRequest.id).where(
                    RefundRequest.order_id == order.id, RefundRequest.status == "SUCCEEDED"
                )
            ),
            "REFUND_EXISTS",
            "该订单已完成退款",
            409,
        )
        # Supersede unfinished requests from the previous release; they never
        # execute on their own, and their history remains available in the DB.
        self.db.execute(
            update(RefundRequest)
            .where(RefundRequest.order_id == order.id, RefundRequest.status.in_(["PENDING", "APPROVED"]))
            .values(status="REJECTED")
        )
        row = RefundRequest(
            order_id=order.id, applicant_id=self.admin.id, amount_minor=order.amount_minor, reason=data.reason
        )
        self.db.add(row)
        self.db.flush()
        BillingService(self.rt, self.db, order.user_id, self.rid).refund(order.id, "admin-refund:" + row.id)
        row.status, row.provider_refund_no = "SUCCEEDED", "dev:" + row.id
        self.audit("ORDER_REFUNDED", order.id, data.reason)
        return fields(row, "id order_id amount_minor status provider_refund_no")

    def quality_create(self, data):
        self.interview(data.interview_id)
        assignee = self.get(AdminUser, data.assignee_id)
        require(
            assignee.status == "ACTIVE" and permits(assignee, "interview:content"),
            "INVALID_ASSIGNEE",
            "受派人需要内容审阅权限",
            400,
        )
        row = QualityReview(**data.model_dump(), created_by=self.admin.id, tags=[])
        self.db.add(row)
        self.db.flush()
        self.audit("QUALITY_ASSIGNED", row.id, data.reason)
        return fields(row, QUALITY_FIELDS)

    def quality_update(self, qid, data):
        row = self.get(QualityReview, qid)
        require(
            self.admin.role == "SUPER_ADMIN" or row.assignee_id == self.admin.id,
            "REVIEW_SCOPE_DENIED",
            "只能处理本人负责的质检任务",
            403,
        )
        for k, value in data.model_dump().items():
            setattr(row, k, value)
        self.audit("QUALITY_UPDATED", qid, data.conclusion[:500])
        return fields(row, QUALITY_FIELDS)

    def reconciliation(self, data):
        try:
            day = date.fromisoformat(data.bill_date)
        except ValueError:
            require(False, "INVALID_BILL_DATE", "账单日期无效")
        first = datetime.combine(day, datetime.min.time(), timezone(timedelta(hours=8))).astimezone(
            timezone.utc
        )
        last = first + timedelta(days=1)
        prefix = {"DEV": "dev:", "WECHAT": "wechat:", "ALIPAY": "alipay:"}[data.channel]
        orders = self.db.scalars(
            select(Order).where(
                Order.paid_at >= first, Order.paid_at < last, Order.provider_trade_no.startswith(prefix)
            )
        ).all()
        known = {o.id: o for o in orders}
        errors, seen = [], set()
        for entry in data.rows:
            order = known.get(entry.order_id)
            error = (
                "DUPLICATE"
                if entry.order_id in seen
                else "MISSING_PLATFORM"
                if not order
                else "AMOUNT"
                if order.amount_minor != entry.amount_minor
                else "STATUS"
                if order.status != entry.status
                else None
            )
            if error:
                errors.append((entry.order_id, error, entry.amount_minor))
            seen.add(entry.order_id)
        errors.extend((o.id, "MISSING_CHANNEL", o.amount_minor) for o in orders if o.id not in seen)
        row = Reconciliation(
            channel=data.channel,
            bill_date=data.bill_date,
            status="DIFFERENCES" if errors else "MATCHED",
            total_rows=len(data.rows),
            difference_count=len(errors),
            created_by=self.admin.id,
        )
        self.db.add(row)
        self.db.flush()
        self.db.add_all(
            [
                ReconciliationItem(batch_id=row.id, order_id=oid, difference_type=kind, amount_minor=amount)
                for oid, kind, amount in errors
            ]
        )
        self.audit("RECONCILIATION_IMPORTED", row.id, data.reason)
        return fields(row, "id channel bill_date status total_rows difference_count created_at")

    def admin_create(self, data):
        row = create_admin(self.db, **data.model_dump())
        self.audit("ADMIN_CREATED", row.id)
        return AdminAuth.view(row)

    def admin_update(self, aid, data):
        require(aid != self.admin.id, "ADMIN_SELF_CHANGE", "不能在此修改本人角色或状态", 409)
        row = self.get(AdminUser, aid)
        row.status, row.role = data.status, data.role
        self.db.execute(
            update(AdminSession)
            .where(AdminSession.admin_id == aid, AdminSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        self.audit("ADMIN_UPDATED", aid, data.reason)
        return AdminAuth.view(row)
