from tests.conftest import data, login
from tests.test_admin import admin_login


def test_customer_catalog_uses_published_admin_products_and_exposes_card_kind(client):
    headers, _ = login(client)
    admin, _ = admin_login(client)
    catalog = data(client.get("/api/v1/billing/products", headers=headers))
    assert {item["code"] for item in catalog["items"]} == {"SINGLE", "STARTER", "PACK_10", "MONTHLY"}
    products = [
        ("CATALOG_YEAR", "YEAR"),
        ("CATALOG_MONTH", "MONTH"),
        ("CATALOG_QUARTER", "QUARTER"),
    ]
    for code, kind in products:
        payload = {
            "code": code,
            "name": code,
            "kind": kind,
            "amount_minor": 1000,
            "original_minor": 1200,
            "credits": 5,
            "validity_days": 30,
            "description": "catalog test",
            "reason": "verify configured customer catalog",
        }
        draft = data(
            client.post(
                "/api/v1/admin/products",
                headers={**admin, "Idempotency-Key": code},
                json=payload,
            )
        )
        data(
            client.post(
                f"/api/v1/admin/products/{draft['id']}/publish",
                headers=admin,
                json={"reason": "publish catalog test"},
            )
        )
    configured = data(client.get("/api/v1/billing/products", headers=headers))["items"]
    assert {item["code"] for item in configured} == {code for code, _ in products}
    assert {item["kind"] for item in configured} == {"YEAR", "MONTH", "QUARTER"}
