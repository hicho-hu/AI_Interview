from sqlalchemy import func, select, update

from app.core.errors import require
from app.core.security import utcnow
from app.db.entitlement_models import CreditAllocation, CreditBalance
from app.db.models import Interview, Order, Usage, User
from app.db.repository import owned
from app.modules.shared import Service

MODES = ("TEXT", "VOICE", "VIDEO")
MODE_LABELS = {"TEXT": "文字", "VOICE": "语音", "VIDEO": "视频"}
PRODUCTS = {
    "SINGLE": {
        "name": "单次文字训练",
        "amount_minor": 990,
        "credits": 1,
        "interview_credits": {"TEXT": 1, "VOICE": 0, "VIDEO": 0},
    },
    "STARTER": {
        "name": "多模式体验套餐",
        "amount_minor": 3990,
        "credits": 6,
        "interview_credits": {"TEXT": 3, "VOICE": 2, "VIDEO": 1},
    },
    "PACK_10": {
        "name": "10 次组合训练套餐",
        "amount_minor": 6900,
        "credits": 10,
        "interview_credits": {"TEXT": 5, "VOICE": 3, "VIDEO": 2},
    },
    "MONTHLY": {
        "name": "20 次组合训练套餐",
        "amount_minor": 9900,
        "credits": 20,
        "interview_credits": {"TEXT": 10, "VOICE": 6, "VIDEO": 4},
    },
}


def credit_balances(db, user):
    typed = dict.fromkeys(MODES, 0)
    for row in db.scalars(select(CreditBalance).where(CreditBalance.user_id == user.id)):
        typed[row.mode] = row.remaining
    return {
        "remaining_interviews": user.credits + sum(typed.values()),
        "universal_interviews": user.credits,
        "interview_credits": typed,
    }


class BillingService(Service):
    def lock_user(self):
        # All balance changes for one owner serialize here, including purchases and grants.
        self.db.execute(update(User).where(User.id == self.uid).values(updated_at=utcnow()))
        return self.db.scalar(
            select(User)
            .where(User.id == self.uid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def change_balance(self, mode, quantity):
        if mode == "UNIVERSAL":
            result = self.db.execute(
                update(User)
                .where(User.id == self.uid, User.credits + quantity >= 0)
                .values(credits=User.credits + quantity)
            )
        else:
            result = self.db.execute(
                update(CreditBalance)
                .where(
                    CreditBalance.user_id == self.uid,
                    CreditBalance.mode == mode,
                    CreditBalance.remaining + quantity >= 0,
                )
                .values(remaining=CreditBalance.remaining + quantity)
            )
            if not result.rowcount and quantity > 0:
                self.db.add(CreditBalance(user_id=self.uid, mode=mode, remaining=quantity))
                self.db.flush()
                return True
        return bool(result.rowcount)

    def allocations(self, usage):
        values = self.db.scalars(select(CreditAllocation).where(CreditAllocation.usage_id == usage.id)).all()
        return {row.mode: row.quantity for row in values} or {"UNIVERSAL": usage.quantity}

    def ledger(self, kind, amounts, status, key, interview_id=None, amount_minor=0):
        row = Usage(
            user_id=self.uid,
            interview_id=interview_id,
            usage_type=kind,
            quantity=sum(amounts.values()),
            amount_minor=amount_minor,
            status=status,
            idempotency_key=key,
        )
        self.db.add(row)
        self.db.flush()
        for mode, quantity in amounts.items():
            if quantity:
                self.db.add(CreditAllocation(usage_id=row.id, mode=mode, quantity=quantity))
        self.db.flush()
        return row

    def entitlements(self, user=None):
        user = user or self.db.get(User, self.uid)
        reserved = dict.fromkeys(MODES, 0)
        for mode, count in self.db.execute(
            select(Interview.mode, func.count())
            .join(Usage, Usage.interview_id == Interview.id)
            .where(Usage.user_id == self.uid, Usage.status == "RESERVED")
            .group_by(Interview.mode)
        ):
            reserved[mode] = count
        return {
            **credit_balances(self.db, user),
            "reserved_interviews": sum(reserved.values()),
            "reserved_by_mode": reserved,
        }

    def reserve(self, interview_id, retry=False):
        interview = owned(self.db, Interview, interview_id, self.uid)
        mode = interview.mode
        self.lock_user()
        source = mode if self.change_balance(mode, -1) else "UNIVERSAL"
        require(
            source != "UNIVERSAL" or self.change_balance("UNIVERSAL", -1),
            "INTERVIEW_QUOTA_NOT_ENOUGH",
            f"{MODE_LABELS[mode]}面试次数不足",
            403,
        )
        if retry:
            ledger = self.db.scalar(select(Usage).where(Usage.idempotency_key == f"interview:{interview_id}"))
            require(ledger and ledger.status == "RELEASED", "BILLING_INVALID_STATE", "面试预占不可重试", 409)
            self.db.query(CreditAllocation).filter(CreditAllocation.usage_id == ledger.id).delete(
                synchronize_session=False
            )
            ledger.status = "RESERVED"
            self.db.add(CreditAllocation(usage_id=ledger.id, mode=source, quantity=-1))
        else:
            self.ledger("INTERVIEW", {source: -1}, "RESERVED", f"interview:{interview_id}", interview_id)

    def settle(self, interview_id, consume):
        self.lock_user()
        row = self.db.scalar(
            select(Usage)
            .where(Usage.user_id == self.uid, Usage.idempotency_key == f"interview:{interview_id}")
            .with_for_update()
        )
        if row and row.status == "RESERVED":
            row.status = "CONSUMED" if consume else "RELEASED"
            if not consume:
                for mode, quantity in self.allocations(row).items():
                    self.change_balance(mode, -quantity)

    def order(self, data, key):
        def create():
            from app.db.admin_models import OrderSnapshot, Product
            from app.modules.growth.service import GrowthService

            version = self.db.scalar(
                select(Product)
                .where(Product.code == data.product_code, Product.status == "PUBLISHED")
                .order_by(Product.version.desc())
                .limit(1)
            )
            product = (
                {
                    name: getattr(version, name)
                    for name in ("name", "amount_minor", "credits", "currency", "interview_credits")
                }
                if version
                else PRODUCTS.get(data.product_code)
            )
            require(product, "PRODUCT_NOT_FOUND", "商品未上架", 404)
            row = Order(
                user_id=self.uid,
                product_code=data.product_code,
                amount_minor=product["amount_minor"],
                credits=product["credits"],
                currency=product.get("currency", "CNY"),
            )
            self.db.add(row)
            self.db.flush()
            self.db.add(
                OrderSnapshot(
                    order_id=row.id,
                    product_id=version.id if version else None,
                    snapshot_json={
                        "code": data.product_code,
                        "version": version.version if version else 0,
                        "promotion": GrowthService(self.rt, self.db, self.uid).promotion_snapshot(),
                        "currency": "CNY",
                        **product,
                    },
                )
            )
            self.db.flush()
            self.audit("ORDER_CREATED", row.id)
            return self.order_view(row)

        return self.idem("order:create", key, data.model_dump(), create)

    def order_credits(self, row):
        from app.db.admin_models import OrderSnapshot

        snapshot = self.db.scalar(select(OrderSnapshot).where(OrderSnapshot.order_id == row.id))
        breakdown = snapshot.snapshot_json.get("interview_credits") if snapshot else None
        return breakdown

    def pay(self, order_id, key):
        require(
            self.rt.settings.environment != "production",
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "真实支付适配器尚未配置",
            503,
        )

        def pay_once():
            self.lock_user()
            row = owned(self.db, Order, order_id, self.uid)
            require(row.status in ("PENDING", "PAID"), "BILLING_INVALID_STATE", "订单不可支付", 409)
            changed = self.db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "PENDING")
                .values(status="PAID", paid_at=utcnow(), provider_trade_no=f"dev:{order_id}")
            )
            if changed.rowcount:
                amounts = self.order_credits(row)
                amounts = amounts if amounts is not None else {"UNIVERSAL": row.credits}
                for mode, quantity in amounts.items():
                    if quantity:
                        self.change_balance(mode, quantity)
                self.ledger(
                    "PURCHASE", amounts, "GRANTED", f"order:{order_id}", amount_minor=row.amount_minor
                )
                from app.modules.growth.service import GrowthService

                GrowthService(self.rt, self.db, self.uid).on_paid(row)
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
            self.lock_user()
            row = owned(self.db, Order, order_id, self.uid)
            require(row.status in ("PAID", "REFUNDED"), "BILLING_INVALID_STATE", "订单不可退款", 409)
            changed = self.db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "PAID")
                .values(status="REFUNDED", refunded_at=utcnow())
            )
            if changed.rowcount:
                amounts = self.order_credits(row)
                amounts = amounts if amounts is not None else {"UNIVERSAL": row.credits}
                for mode, quantity in amounts.items():
                    require(
                        not quantity or self.change_balance(mode, -quantity),
                        "BILLING_CREDITS_USED",
                        "套餐对应类型额度已使用或预占，不能全额退回",
                        409,
                    )
                self.ledger(
                    "REFUND",
                    {mode: -quantity for mode, quantity in amounts.items()},
                    "REFUNDED",
                    f"refund:{order_id}",
                    amount_minor=-row.amount_minor,
                )
            from app.modules.growth.service import GrowthService

            GrowthService(self.rt, self.db, self.uid).on_refund(row)
            self.db.refresh(row)
            self.audit("ORDER_REFUNDED", row.id)
            return self.order_view(row)

        return self.idem(f"order:refund:{order_id}", key, {}, refund_once)

    def order_view(self, row):
        return {
            **{
                name: getattr(row, name)
                for name in (
                    "id",
                    "product_code",
                    "amount_minor",
                    "credits",
                    "currency",
                    "status",
                    "paid_at",
                    "refunded_at",
                )
            },
            "interview_credits": self.order_credits(row),
        }
