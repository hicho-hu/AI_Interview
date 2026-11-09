from sqlalchemy import func, select

from app.db.growth_models import InvitationReward, PromotionCommission, ReferralRegistration
from tests.conftest import data, login
from tests.test_admin import admin_login
from tests.test_email_accounts import register_email


def test_invitation_configuration_grants_typed_rewards_only_at_each_threshold(client):
    inviter, uid = login(client, "inviter")
    admin, _ = admin_login(client)
    config = {
        "enabled": True,
        "threshold": 2,
        "inviter_rewards": {"TEXT": 2, "VOICE": 1, "VIDEO": 0},
        "invitee_rewards": {"TEXT": 0, "VOICE": 1, "VIDEO": 0},
        "reason": "配置邀请奖励验证规则",
    }
    saved = data(client.put("/api/v1/admin/invitations/config", headers=admin, json=config))
    assert saved["version"] == 1
    invitation = data(client.get("/api/v1/invitations/me", headers=inviter))
    assert data(client.get("/api/v1/invitations/me", headers=inviter))["code"] == invitation["code"]
    for index in range(4):
        invited, result, body = register_email(
            client, f"invited{index}@example.com", invite_code=invitation["code"]
        )
        assert result["user"]["interview_credits"] == {"TEXT": 0, "VOICE": 1, "VIDEO": 0}
        assert client.post("/api/v1/auth/email-register", json=body).status_code == 409
        current = data(client.get("/api/v1/billing/entitlements", headers=inviter))
        assert current["interview_credits"]["TEXT"] == ((index + 1) // 2) * 2
        assert current["interview_credits"]["VOICE"] == (index + 1) // 2
    dashboard = data(client.get("/api/v1/invitations/me", headers=inviter))
    assert dashboard["valid_invites"] == 4 and dashboard["reward_count"] == 2
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(func.count()).select_from(ReferralRegistration)) == 4
        assert db.scalar(select(func.count()).select_from(InvitationReward)) == 2


def test_promotion_order_rate_snapshot_commission_idempotency_and_refund_reversal(client):
    admin, _ = admin_login(client)
    payload = {
        "name": "面试训练推广",
        "promoter_name": "合作推广人",
        "alipay_account": "synthetic-promoter@example.test",
        "rate_bps": 1250,
        "status": "ACTIVE",
        "reason": "建立分佣推广链接",
    }
    promotion = data(client.post("/api/v1/admin/promotions", headers=admin, json=payload), 201)
    buyer, result, _ = register_email(client, "buyer@example.com", promotion_code=promotion["code"])
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**buyer, "Idempotency-Key": "purchase"},
            json={"product_code": "SINGLE"},
        )
    )
    data(
        client.put(
            f"/api/v1/admin/promotions/{promotion['id']}",
            headers={**admin, "Idempotency-Key": "update-rate"},
            json={**payload, "rate_bps": 5000},
        )
    )
    for key in ("pay", "pay-again"):
        data(
            client.post(
                f"/api/v1/billing/orders/{order['id']}/dev-pay", headers={**buyer, "Idempotency-Key": key}
            )
        )
    commissions = data(client.get("/api/v1/admin/promotions/commissions/records", headers=admin))
    assert commissions["total"] == 1 and commissions["items"][0]["amount_minor"] == 123
    assert commissions["items"][0]["rate_bps"] == 1250
    data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/refund", headers={**buyer, "Idempotency-Key": "refund"}
        )
    )
    current = data(client.get("/api/v1/admin/promotions", headers=admin))["items"][0]
    assert (
        current["registrations"] == 1 and current["accrued_minor"] == 0 and current["reversed_minor"] == 123
    )
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(func.count()).select_from(PromotionCommission)) == 1
        assert db.scalar(select(PromotionCommission)).status == "REVERSED"
    support, _ = admin_login(client, "support", "SUPPORT")
    assert client.get("/api/v1/admin/promotions", headers=support).status_code == 403
