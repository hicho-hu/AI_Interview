from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.core.errors import BusinessError
from app.db.entitlement_models import CreditAllocation
from app.db.models import Order, Usage, User
from app.modules.interview.service import InterviewService
from app.providers.ai import MockProvider
from app.schemas.contracts import InterviewInput
from tests.conftest import data, interview, job, login
from tests.test_admin import admin_login
from tests.test_billing_tasks import FailingProvider, exhaust

BUNDLE = {"TEXT": 3, "VOICE": 2, "VIDEO": 1}


def test_failed_preparation_returns_typed_credit_and_retry_reserves_it_again(client):
    headers, uid = typed_user(client)
    buy(client, headers, "SINGLE")
    job_id = job(client, headers)
    rt = client.app.state.runtime
    rt.provider = FailingProvider()
    created = data(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": "failed-plan"},
            json={"job_profile_id": job_id, "config": {"mode": "TEXT"}},
        )
    )
    exhaust(rt, created["task_id"])
    assert balance(client, headers)["interview_credits"]["TEXT"] == 1
    assert balance(client, headers)["reserved_interviews"] == 0
    rt.provider = MockProvider()
    data(client.post(f"/api/v1/tasks/{created['task_id']}/retry", headers=headers))
    assert (
        data(client.get(f"/api/v1/interviews/{created['interview_id']}", headers=headers))["status"]
        == "READY"
    )
    assert balance(client, headers)["interview_credits"]["TEXT"] == 0
    assert balance(client, headers)["reserved_by_mode"]["TEXT"] == 1


def test_concurrent_creation_cannot_spend_the_same_typed_credit_twice(client):
    headers, uid = typed_user(client)
    buy(client, headers, "SINGLE")
    job_id = job(client, headers)
    rt = client.app.state.runtime

    def create_one(index):
        try:
            with rt.db.session() as db:
                return InterviewService(rt, db, uid).create(
                    InterviewInput(job_profile_id=job_id, config={"mode": "TEXT"}), f"concurrent-{index}"
                )
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(create_one, range(3)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("INTERVIEW_QUOTA_NOT_ENOUGH") == 2
    assert balance(client, headers)["interview_credits"]["TEXT"] == 0
    assert balance(client, headers)["reserved_by_mode"]["TEXT"] == 1


def balance(client, headers):
    return data(client.get("/api/v1/billing/entitlements", headers=headers))


def buy(client, headers, product="STARTER", key="order"):
    order = data(
        client.post(
            "/api/v1/billing/orders",
            headers={**headers, "Idempotency-Key": key},
            json={"product_code": product},
        )
    )
    return data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/dev-pay",
            headers={**headers, "Idempotency-Key": "pay-" + key},
        )
    )


def typed_user(client):
    headers, uid = login(client)
    with client.app.state.runtime.db.session() as db:
        db.get(User, uid).credits = 0
    return headers, uid


def test_bundle_grants_each_mode_once_and_preserves_order_snapshot(client):
    headers, uid = typed_user(client)
    order = buy(client, headers)
    assert order["interview_credits"] == BUNDLE
    for key in ("pay-again", "pay-again"):
        data(
            client.post(
                f"/api/v1/billing/orders/{order['id']}/dev-pay", headers={**headers, "Idempotency-Key": key}
            )
        )
    result = balance(client, headers)
    assert result["interview_credits"] == BUNDLE
    assert result["remaining_interviews"] == 6 and result["universal_interviews"] == 0
    assert data(client.get("/api/v1/users/me", headers=headers))["interview_credits"] == BUNDLE
    ledger = data(client.get("/api/v1/billing/usage", headers=headers))["items"]
    purchases = [entry for entry in ledger if entry["usage_type"] == "PURCHASE"]
    assert len(purchases) == 1 and purchases[0]["credit_allocations"] == BUNDLE
    other, _ = login(client, "other")
    assert client.get(f"/api/v1/billing/orders/{order['id']}", headers=other).status_code == 404
    refunded = data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/refund", headers={**headers, "Idempotency-Key": "refund"}
        )
    )
    assert refunded["interview_credits"] == BUNDLE
    assert balance(client, headers)["remaining_interviews"] == 0
    data(
        client.post(
            f"/api/v1/billing/orders/{order['id']}/refund",
            headers={**headers, "Idempotency-Key": "refund-again"},
        )
    )
    assert balance(client, headers)["remaining_interviews"] == 0


def test_reservation_uses_only_selected_mode_and_cancellation_restores_same_source(client):
    headers, _ = typed_user(client)
    buy(client, headers)
    row = interview(client, headers, config={"mode": "VIDEO"}, key="video")
    result = balance(client, headers)
    assert result["interview_credits"] == {"TEXT": 3, "VOICE": 2, "VIDEO": 0}
    assert result["reserved_by_mode"] == {"TEXT": 0, "VOICE": 0, "VIDEO": 1}
    denied = client.post(
        "/api/v1/interviews",
        headers={**headers, "Idempotency-Key": "no-video"},
        json={"job_profile_id": row["job_profile_id"], "config": {"mode": "VIDEO"}},
    )
    assert denied.status_code == 403 and denied.json()["code"] == "INTERVIEW_QUOTA_NOT_ENOUGH"
    assert balance(client, headers)["remaining_interviews"] == 5
    for key in ("cancel", "cancel-again"):
        data(
            client.post(f"/api/v1/interviews/{row['id']}/cancel", headers={**headers, "Idempotency-Key": key})
        )
    assert balance(client, headers)["interview_credits"] == BUNDLE
    assert balance(client, headers)["reserved_interviews"] == 0


def test_typed_credits_take_priority_and_legacy_credits_remain_usable(client):
    headers, _ = login(client)
    buy(client, headers, "SINGLE")
    text = interview(client, headers, config={"mode": "TEXT"}, key="text")
    assert balance(client, headers)["universal_interviews"] == 3
    assert balance(client, headers)["interview_credits"]["TEXT"] == 0
    video = interview(client, headers, config={"mode": "VIDEO"}, key="video")
    assert balance(client, headers)["universal_interviews"] == 2
    data(
        client.post(
            f"/api/v1/interviews/{video['id']}/cancel", headers={**headers, "Idempotency-Key": "cancel-video"}
        )
    )
    assert balance(client, headers)["universal_interviews"] == 3
    data(client.post(f"/api/v1/interviews/{text['id']}/start", headers=headers))
    data(
        client.post(
            f"/api/v1/interviews/{text['id']}/cancel",
            headers={**headers, "Idempotency-Key": "cancel-started"},
        )
    )
    assert balance(client, headers)["interview_credits"]["TEXT"] == 0
    assert balance(client, headers)["universal_interviews"] == 3


def test_refund_cannot_use_other_modes_or_universal_balance_to_cover_consumed_type(client):
    headers, uid = login(client)
    order = buy(client, headers)
    row = interview(client, headers, config={"mode": "VIDEO"})
    data(client.post(f"/api/v1/interviews/{row['id']}/start", headers=headers))
    response = client.post(
        f"/api/v1/billing/orders/{order['id']}/refund", headers={**headers, "Idempotency-Key": "used-refund"}
    )
    assert response.status_code == 409
    assert balance(client, headers)["interview_credits"] == {"TEXT": 3, "VOICE": 2, "VIDEO": 0}
    assert balance(client, headers)["universal_interviews"] == 3
    with client.app.state.runtime.db.session() as db:
        assert db.get(Order, order["id"]).status == "PAID"
        assert db.scalar(select(Usage.id).where(Usage.user_id == uid, Usage.usage_type == "REFUND")) is None


def test_admin_can_publish_bundle_and_grant_a_specific_mode(client):
    headers, uid = typed_user(client)
    admin, _ = admin_login(client)
    payload = {
        "code": "CUSTOM_BUNDLE",
        "name": "组合训练",
        "kind": "PACK",
        "amount_minor": 3000,
        "original_minor": 4000,
        "interview_credits": BUNDLE,
        "validity_days": 30,
        "reason": "发布文字语音视频组合套餐",
    }
    for invalid in ({"TEXT": 0, "VOICE": 0, "VIDEO": 0}, {"TEXT": -1, "VOICE": 2, "VIDEO": 1}):
        assert (
            client.post(
                "/api/v1/admin/products", headers=admin, json={**payload, "interview_credits": invalid}
            ).status_code
            == 422
        )
    product = data(client.post("/api/v1/admin/products", headers=admin, json=payload))
    assert product["credits"] == 6 and product["interview_credits"] == BUNDLE
    data(
        client.post(
            f"/api/v1/admin/products/{product['id']}/publish",
            headers=admin,
            json={"reason": "立即发布分类权益商品"},
        )
    )
    order = buy(client, headers, "CUSTOM_BUNDLE")
    # A new version with the same total cannot change an already-created order's split.
    next_payload = {**payload, "interview_credits": {"TEXT": 5, "VOICE": 0, "VIDEO": 1}}
    next_product = data(
        client.post("/api/v1/admin/products", headers={**admin, "Idempotency-Key": "v2"}, json=next_payload)
    )
    data(
        client.post(
            f"/api/v1/admin/products/{next_product['id']}/publish",
            headers={**admin, "Idempotency-Key": "publish-v2"},
            json={"reason": "发布新的次数组合版本"},
        )
    )
    assert (
        data(client.get(f"/api/v1/billing/orders/{order['id']}", headers=headers))["interview_credits"]
        == BUNDLE
    )
    grant = {"mode": "VOICE", "quantity": 2, "reason": "补发语音服务异常次数"}
    for _ in range(2):
        data(client.post(f"/api/v1/admin/users/{uid}/entitlements", headers=admin, json=grant))
    assert balance(client, headers)["interview_credits"] == {"TEXT": 3, "VOICE": 4, "VIDEO": 1}
    assert balance(client, headers)["universal_interviews"] == 0
    detail = data(client.get(f"/api/v1/admin/users/{uid}", headers=admin))
    assert detail["interview_credits"]["VOICE"] == 4
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(
            select(CreditAllocation.id)
            .join(Usage, CreditAllocation.usage_id == Usage.id)
            .where(Usage.user_id == uid, Usage.usage_type == "ADMIN_GRANT", CreditAllocation.mode == "VOICE")
        )
