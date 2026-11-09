from datetime import timedelta

from sqlalchemy import select

from app.core.security import utcnow
from app.db.admin_models import (
    AdminAudit,
    AdminIdempotency,
    AdminUser,
    Approval,
    OrderSnapshot,
    PaymentConfig,
    Product,
    RefundRequest,
)
from app.db.models import MediaAsset, Order, Usage, User
from app.modules.admin.auth import create_admin
from app.modules.admin.security import totp
from tests.conftest import data, interview, login

PASSWORD = "Admin-test-password-123"


def admin_login(client, name="admin", role="SUPER_ADMIN"):
    rt = client.app.state.runtime
    with rt.db.session() as db:
        row = db.scalar(select(AdminUser).where(AdminUser.login_name == name))
        if not row:
            row = create_admin(db, name, name, PASSWORD, role)
        aid = row.id
    result = data(client.post("/api/v1/admin/auth/login", json={"login_name": name, "password": PASSWORD}))
    return {"Authorization": "Bearer " + result["access_token"], "Idempotency-Key": "test-key"}, aid


def test_admin_identity_roles_and_revocation(client):
    user, _ = login(client)
    assert client.get("/api/v1/admin/users", headers=user).status_code == 401
    headers, _ = admin_login(client, "finance", "FINANCE")
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    data(client.get("/api/v1/admin/orders", headers=headers))
    data(client.post("/api/v1/admin/auth/logout", headers=headers))
    assert client.get("/api/v1/admin/orders", headers=headers).status_code == 401
    root, _ = admin_login(client)
    logs = data(client.get("/api/v1/admin/audit-logs?q=ACCESS_DENIED", headers=root))
    assert logs["total"] == 1


def test_admin_freeze_direct_grant_and_idempotency(client):
    user, uid = login(client)
    root, _ = admin_login(client)
    payload = {"quantity": 5, "reason": "补发系统异常额度"}
    result = data(client.post(f"/api/v1/admin/users/{uid}/entitlements", json=payload, headers=root))
    assert result["status"] == "GRANTED" and result["remaining_interviews"] == 8
    assert data(client.get("/api/v1/users/me", headers=user))["remaining_interviews"] == 8
    assert (
        data(client.post(f"/api/v1/admin/users/{uid}/entitlements", json=payload, headers=root))["id"]
        == result["id"]
    )
    assert (
        client.post(
            f"/api/v1/admin/users/{uid}/entitlements", json={**payload, "quantity": 6}, headers=root
        ).status_code
        == 409
    )
    with client.app.state.runtime.db.session() as db:
        assert (
            len(
                db.scalars(select(Usage).where(Usage.user_id == uid, Usage.usage_type == "ADMIN_GRANT")).all()
            )
            == 1
        )
        assert db.scalar(select(Approval.id)) is None
        assert db.scalar(select(AdminAudit.id).where(AdminAudit.action == "ENTITLEMENT_GRANTED"))
    data(
        client.patch(
            f"/api/v1/admin/users/{uid}/status",
            headers=root,
            json={"status": "FROZEN", "reason": "核查账户异常活动"},
        )
    )
    assert client.get("/api/v1/users/me", headers=user).status_code == 401
    assert client.get("/api/v1/admin/users?page_size=101", headers=root).status_code == 422


def product_payload(amount):
    return {
        "code": "MEMBER_YEAR",
        "name": "年度训练包",
        "kind": "YEAR",
        "amount_minor": amount,
        "original_minor": 99900,
        "credits": 100,
        "validity_days": 365,
        "description": "年度总次数",
        "reason": "发布年度训练套餐",
    }


def publish_product(client, root, amount):
    root = {**root, "Idempotency-Key": "product-" + str(amount)}
    product = data(client.post("/api/v1/admin/products", headers=root, json=product_payload(amount)))
    result = data(
        client.post(
            f"/api/v1/admin/products/{product['id']}/publish",
            headers=root,
            json={"reason": "直接发布年度商品"},
        )
    )
    assert result["status"] == "PUBLISHED" and result["id"] == product["id"]
    assert (
        data(
            client.post(
                f"/api/v1/admin/products/{product['id']}/publish",
                headers=root,
                json={"reason": "直接发布年度商品"},
            )
        )["id"]
        == product["id"]
    )
    return product


def test_price_snapshots_and_refund_ledger(client):
    user, uid = login(client)
    root, _ = admin_login(client)
    publish_product(client, root, 39900)
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**user, "Idempotency-Key": "order-1"},
            json={"product_code": "MEMBER_YEAR"},
        )
    )
    publish_product(client, root, 49900)
    assert data(client.get("/api/v1/billing/orders/" + order["id"], headers=user))["amount_minor"] == 39900
    with client.app.state.runtime.db.session() as db:
        snapshot = db.scalar(select(OrderSnapshot).where(OrderSnapshot.order_id == order["id"]))
        assert snapshot.snapshot_json["version"] == 1
    data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/dev-pay", headers={**user, "Idempotency-Key": "pay"}
        )
    )
    refund = data(
        client.post(
            "/api/v1/admin/refunds",
            headers=root,
            json={"order_id": order["id"], "reason": "按用户申请全额退款"},
        )
    )
    assert refund["status"] == "SUCCEEDED" and "approval_id" not in refund
    assert (
        data(
            client.post(
                "/api/v1/admin/refunds",
                headers=root,
                json={"order_id": order["id"], "reason": "按用户申请全额退款"},
            )
        )["id"]
        == refund["id"]
    )
    assert data(client.get("/api/v1/billing/orders/" + order["id"], headers=user))["status"] == "REFUNDED"
    assert data(client.get("/api/v1/users/me", headers=user))["remaining_interviews"] == 3
    with client.app.state.runtime.db.session() as db:
        rows = db.scalars(select(Usage).where(Usage.user_id == uid, Usage.usage_type == "REFUND")).all()
        assert len(rows) == 1


def test_media_review_skips_user_consent_but_keeps_role_scope_expiry_and_no_url_cache(client):
    user, uid = login(client)
    row = interview(client, user)
    root, _ = admin_login(client)
    quality, qid = admin_login(client, "quality", "QUALITY")
    rt = client.app.state.runtime
    with rt.db.session() as db:
        asset = MediaAsset(
            user_id=uid,
            interview_id=row["id"],
            media_type="SESSION_AUDIO",
            object_key="test/audio.wav",
            filename="recording.wav",
            mime_type="audio/wav",
            size_bytes=4,
            duration_ms=1000,
            status="READY",
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(asset)
        db.flush()
        mid = asset.id
    path = f"/api/v1/admin/media/{mid}/playback-grant"
    assert client.post(path, headers=root, json={"reason": "短"}).status_code == 422
    result = data(client.post(path, headers=root, json={"reason": "管理员直接审阅面试记录"}))
    assert result["expires_in"] <= 120 and "signature=" in result["url"]
    assert client.post(path, headers=quality, json={"reason": "用户授权支持排障"}).status_code == 403
    data(
        client.post(
            "/api/v1/admin/quality-tasks",
            headers=root,
            json={"interview_id": row["id"], "assignee_id": qid, "reason": "用户授权支持排障"},
        )
    )
    result = data(client.post(path, headers=quality, json={"reason": "分派质检任务并直接审阅"}))
    assert result["expires_in"] <= 120 and "signature=" in result["url"]
    content = data(
        client.post(
            f"/api/v1/admin/interviews/{row['id']}/review",
            headers=quality,
            json={"reason": "查看面试时间轴"},
        )
    )
    assert "turns" in content
    with rt.db.session() as db:
        db.get(MediaAsset, mid).expires_at = utcnow() - timedelta(seconds=1)
        assert all("signature=" not in r.reason for r in db.scalars(select(AdminAudit)))
    assert client.post(path, headers=quality, json={"reason": "回放已过期录制"}).status_code == 410


def test_mfa_and_disabled_session(client):
    headers, aid = admin_login(client)
    setup = data(client.post("/api/v1/admin/auth/mfa/setup", headers=headers))
    data(
        client.post(
            "/api/v1/admin/auth/mfa/confirm",
            headers=headers,
            json={"secret": setup["secret"], "code": totp(setup["secret"])},
        )
    )
    assert (
        client.post(
            "/api/v1/admin/auth/login", json={"login_name": "admin", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/admin/auth/login",
            json={"login_name": "admin", "password": PASSWORD, "mfa_code": totp(setup["secret"])},
        ).status_code
        == 401
    )
    reviewer, _ = admin_login(client, "reviewer")
    data(
        client.patch(
            f"/api/v1/admin/admin-users/{aid}",
            headers=reviewer,
            json={"role": "SUPER_ADMIN", "status": "DISABLED", "reason": "停用离职管理员账号"},
        )
    )
    assert client.get("/api/v1/admin/auth/me", headers=headers).status_code == 401


def test_metrics_empty_and_reconciliation(client):
    root, _ = admin_login(client)
    result = data(client.get("/api/v1/admin/dashboard/overview", headers=root))
    assert result["new_users"] == result["net_minor"] == result["completion_rate"] == 0
    assert len(data(client.get("/api/v1/admin/dashboard/trends", headers=root))["items"]) == 7
    assert (
        client.get(
            "/api/v1/admin/dashboard/overview?start=2025-01-01&end=2026-01-01", headers=root
        ).status_code
        == 400
    )
    assert client.get("/api/v1/admin/dashboard/overview?timezone=invalid", headers=root).status_code == 400
    result = data(
        client.post(
            "/api/v1/admin/reconciliations/run",
            headers=root,
            json={
                "channel": "DEV",
                "bill_date": "2026-11-01",
                "reason": "导入沙箱订单对账单",
                "rows": [{"order_id": "missing", "amount_minor": 990, "status": "PAID"}],
            },
        )
    )
    assert result["difference_count"] == 1
    item = data(client.get(f"/api/v1/admin/reconciliations/{result['id']}/items", headers=root))["items"][0]
    assert item["difference_type"] == "MISSING_PLATFORM"
    data(
        client.patch(
            f"/api/v1/admin/reconciliation-items/{item['id']}",
            headers=root,
            json={"reason": "已确认渠道账单重复导入"},
        )
    )


def test_direct_payment_configuration_and_permissions(client):
    finance, _ = admin_login(client, "finance", "FINANCE")
    payload = {
        "environment": "sandbox",
        "merchant_id": "merchant-test",
        "app_id": "app-test",
        "secret_ref": "kms://payments/test-secret",
        "notify_url": "https://api.example.com/notify",
        "enabled": False,
        "reason": "保存沙箱支付配置",
    }
    response = data(client.put("/api/v1/admin/payment-channels/WECHAT", headers=finance, json=payload))
    assert response["version"] == 1 and not response["enabled"]
    assert (
        data(client.put("/api/v1/admin/payment-channels/WECHAT", headers=finance, json=payload)) == response
    )
    assert data(client.get("/api/v1/admin/payment-channels", headers=finance))["items"][0]["version"] == 1
    second = data(
        client.put(
            "/api/v1/admin/payment-channels/WECHAT",
            headers={**finance, "Idempotency-Key": "next"},
            json=payload,
        )
    )
    assert second["version"] == 2
    assert (
        client.put(
            "/api/v1/admin/payment-channels/WECHAT",
            headers={**finance, "Idempotency-Key": "enable"},
            json={**payload, "enabled": True},
        ).status_code
        == 409
    )
    support, _ = admin_login(client, "support", "SUPPORT")
    assert (
        client.put("/api/v1/admin/payment-channels/WECHAT", headers=support, json=payload).status_code == 403
    )
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(Approval.id)) is None
        config = db.scalar(select(PaymentConfig))
        assert "merchant-test" not in config.config_encrypted
        assert db.scalar(select(AdminAudit.id).where(AdminAudit.action == "PAYMENT_CONFIG_SAVED"))


def test_approval_routes_removed_and_legacy_product_can_publish(client):
    root, aid = admin_login(client)
    assert client.get("/api/v1/admin/approvals", headers=root).status_code == 404
    assert (
        client.post(
            "/api/v1/admin/approvals/old/review",
            headers=root,
            json={"approve": True, "reason": "尝试使用历史审批接口"},
        ).status_code
        == 404
    )
    schema = client.get("/openapi.json").json()
    assert not any("/admin/approvals" in path for path in schema["paths"])
    assert not any(
        p.startswith("approval:")
        for r in data(client.get("/api/v1/admin/roles", headers=root))["items"]
        for p in r["permissions"]
    )
    product = data(client.post("/api/v1/admin/products", headers=root, json=product_payload(39900)))
    with client.app.state.runtime.db.session() as db:
        db.get(Product, product["id"]).status = "IN_REVIEW"
        db.add(
            Approval(
                applicant_id=aid,
                kind="PRODUCT_PUBLISH",
                resource_id=product["id"],
                payload_encrypted="historical",
                reason="历史待审批记录",
            )
        )
    result = data(
        client.post(
            f"/api/v1/admin/products/{product['id']}/publish",
            headers=root,
            json={"reason": "直接发布历史待处理商品"},
        )
    )
    assert result["status"] == "PUBLISHED"
    with client.app.state.runtime.db.session() as db:
        assert len(db.scalars(select(Approval)).all()) == 1
        assert db.scalar(select(Approval)).status == "PENDING"


def test_refund_failure_does_not_create_pending_requests(client):
    user, uid = login(client)
    finance, _ = admin_login(client, "finance", "FINANCE")
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**user, "Idempotency-Key": "order"},
            json={"product_code": "SINGLE"},
        )
    )
    data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/dev-pay", headers={**user, "Idempotency-Key": "pay"}
        )
    )
    rt = client.app.state.runtime
    payload = {"order_id": order["id"], "reason": "用户要求退回未使用额度"}
    with rt.db.session() as db:
        db.get(Order, order["id"]).provider_trade_no = "wechat:live-order"
    assert client.post("/api/v1/admin/refunds", headers=finance, json=payload).status_code == 503
    with rt.db.session() as db:
        assert db.scalar(select(RefundRequest.id)) is None
        assert db.get(Order, order["id"]).status == "PAID"
        db.get(Order, order["id"]).provider_trade_no = "dev:" + order["id"]
        db.get(User, uid).credits = 0
        from app.db.entitlement_models import CreditBalance

        balance = db.scalar(
            select(CreditBalance).where(CreditBalance.user_id == uid, CreditBalance.mode == "TEXT")
        )
        balance.remaining = 0
    assert client.post("/api/v1/admin/refunds", headers=finance, json=payload).status_code == 409
    with rt.db.session() as db:
        assert db.scalar(select(RefundRequest.id)) is None
        assert db.get(Order, order["id"]).status == "PAID"
        assert db.scalar(select(Usage.id).where(Usage.usage_type == "REFUND")) is None
        assert db.scalar(select(Approval.id)) is None


def test_direct_grant_ignores_old_pending_response_and_preserves_roles(client):
    import hashlib
    import json

    user, uid = login(client)
    support, aid = admin_login(client, "support", "SUPPORT")
    rt = client.app.state.runtime
    payload = {"quantity": 2, "reason": "直接补发修复失败额度"}
    with rt.db.session() as db:
        db.add(
            AdminIdempotency(
                admin_id=aid,
                scope="user:grant:" + uid,
                key="test-key",
                request_hash=hashlib.sha256(b"old").hexdigest(),
                response_encrypted=rt.security.encrypt(json.dumps({"status": "PENDING"})),
            )
        )
    result = data(client.post(f"/api/v1/admin/users/{uid}/entitlements", headers=support, json=payload))
    assert result["status"] == "GRANTED"
    assert data(client.get("/api/v1/users/me", headers=user))["remaining_interviews"] == 5
    quality, _ = admin_login(client, "quality", "QUALITY")
    assert (
        client.post(f"/api/v1/admin/users/{uid}/entitlements", headers=quality, json=payload).status_code
        == 403
    )
    assert data(client.get("/api/v1/users/me", headers=user))["remaining_interviews"] == 5


def test_legacy_pending_refund_can_be_completed_directly(client):
    user, _ = login(client)
    root, aid = admin_login(client)
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**user, "Idempotency-Key": "order"},
            json={"product_code": "SINGLE"},
        )
    )
    data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/dev-pay", headers={**user, "Idempotency-Key": "pay"}
        )
    )
    with client.app.state.runtime.db.session() as db:
        old = RefundRequest(order_id=order["id"], applicant_id=aid, amount_minor=990, reason="历史待审批退款")
        db.add(old)
        db.flush()
        old_id = old.id
    result = data(
        client.post(
            "/api/v1/admin/refunds",
            headers=root,
            json={"order_id": order["id"], "reason": "直接执行历史退款事项"},
        )
    )
    assert result["status"] == "SUCCEEDED"
    with client.app.state.runtime.db.session() as db:
        assert db.get(RefundRequest, old_id).status == "REJECTED"
        assert db.get(RefundRequest, result["id"]).status == "SUCCEEDED"
