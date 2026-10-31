from sqlalchemy import select, update

from app.core.errors import require
from app.core.security import utcnow
from app.db.models import Order, Usage, User
from app.db.repository import owned
from app.modules.shared import Service

PRODUCTS = {
    "SINGLE": {"name": "单次训练", "amount_minor": 990, "credits": 1},
    "PACK_10": {"name": "10 次训练套餐", "amount_minor": 6900, "credits": 10},
    "MONTHLY": {"name": "月度训练额度包", "amount_minor": 9900, "credits": 20},
}


class BillingService(Service):
    def reserve(self, interview_id):
        result = self.db.execute(
            update(User).where(User.id == self.uid, User.credits > 0).values(credits=User.credits - 1)
        )
        require(result.rowcount == 1, "INTERVIEW_QUOTA_NOT_ENOUGH", "面试额度不足", 403)
        self.db.add(
            Usage(
                user_id=self.uid,
                interview_id=interview_id,
                usage_type="INTERVIEW",
                quantity=-1,
                status="RESERVED",
                idempotency_key=f"interview:{interview_id}",
            )
        )

    def settle(self, interview_id, consume):
        row = self.db.scalar(
            select(Usage).where(Usage.idempotency_key == f"interview:{interview_id}").with_for_update()
        )
        if row and row.status == "RESERVED":
            row.status = "CONSUMED" if consume else "RELEASED"
            if not consume:
                self.db.execute(update(User).where(User.id == self.uid).values(credits=User.credits + 1))

    def order(self, data, key):
        def create():
            product = PRODUCTS[data.product_code]
            row = Order(
                user_id=self.uid,
                product_code=data.product_code,
                amount_minor=product["amount_minor"],
                credits=product["credits"],
            )
            self.db.add(row)
            self.db.flush()
            self.audit("ORDER_CREATED", row.id)
            return self.order_view(row)

        return self.idem("order:create", key, data.model_dump(), create)

    def pay(self, order_id, key):
        require(
            self.rt.settings.environment != "production",
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "真实支付适配器尚未配置",
            503,
        )

        def pay_once():
            row = owned(self.db, Order, order_id, self.uid)
            require(row.status in ("PENDING", "PAID"), "BILLING_INVALID_STATE", "订单不可支付", 409)
            changed = self.db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "PENDING")
                .values(status="PAID", paid_at=utcnow(), provider_trade_no=f"dev:{order_id}")
            )
            if changed.rowcount:
                self.db.execute(
                    update(User).where(User.id == self.uid).values(credits=User.credits + row.credits)
                )
                self.db.add(
                    Usage(
                        user_id=self.uid,
                        usage_type="PURCHASE",
                        quantity=row.credits,
                        amount_minor=row.amount_minor,
                        status="GRANTED",
                        idempotency_key=f"order:{order_id}",
                    )
                )
            self.db.refresh(row)
            self.audit("DEV_ORDER_PAID", row.id)
            return self.order_view(row)

        return self.idem(f"order:pay:{order_id}", key, {}, pay_once)

    def refund(self, order_id, key):
        require(
            self.rt.settings.environment != "production",
            "BILLING_REFUND_NOT_CONFIGURED",
            "真实退款适配器尚未配置",
            503,
        )

        def refund_once():
            row = owned(self.db, Order, order_id, self.uid)
            require(row.status in ("PAID", "REFUNDED"), "BILLING_INVALID_STATE", "订单不可退款", 409)
            changed = self.db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "PAID")
                .values(status="REFUNDED", refunded_at=utcnow())
            )
            if changed.rowcount:
                result = self.db.execute(
                    update(User)
                    .where(User.id == self.uid, User.credits >= row.credits)
                    .values(credits=User.credits - row.credits)
                )
                require(result.rowcount == 1, "BILLING_CREDITS_USED", "套餐额度已使用，不能全额退回", 409)
                self.db.add(
                    Usage(
                        user_id=self.uid,
                        usage_type="REFUND",
                        quantity=-row.credits,
                        amount_minor=-row.amount_minor,
                        status="REFUNDED",
                        idempotency_key=f"refund:{order_id}",
                    )
                )
            self.db.refresh(row)
            self.audit("ORDER_REFUNDED", row.id)
            return self.order_view(row)

        return self.idem(f"order:refund:{order_id}", key, {}, refund_once)

    @staticmethod
    def order_view(row):
        return {
            name: getattr(row, name)
            for name in [
                "id",
                "product_code",
                "amount_minor",
                "credits",
                "currency",
                "status",
                "paid_at",
                "refunded_at",
            ]
        }
