"""Admin ORM mappings; business users, interviews and orders remain shared."""

from sqlalchemy.orm import registry

from app.db.admin_schema_v1 import define_tables
from app.db.entitlement_schema_v1 import product_credits_column

mapper_registry = registry()


def register(metadata):
    names = define_tables(metadata)
    metadata.tables["product_skus"].append_column(product_credits_column())
    mapping = {
        "AdminUser": "admin_users",
        "AdminSession": "admin_sessions",
        "AdminAudit": "admin_audit_logs",
        "AdminIdempotency": "admin_idempotency_records",
        "Approval": "admin_approval_requests",
        "Product": "product_skus",
        "OrderSnapshot": "order_product_snapshots",
        "PaymentConfig": "payment_channel_configs",
        "RefundRequest": "refund_requests",
        "Reconciliation": "reconciliation_batches",
        "ReconciliationItem": "reconciliation_items",
        "QualityReview": "quality_review_tasks",
    }
    for classname, tablename in mapping.items():
        cls = type(classname, (), {})
        mapper_registry.map_imperatively(cls, metadata.tables[tablename])
        globals()[classname] = cls
    return names


from app.db.models import Base  # noqa: E402

register(Base.metadata)
