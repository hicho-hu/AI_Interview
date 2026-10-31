from typing import Annotated, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from app.schemas.contracts import DTO, Text

Role = Literal["SUPER_ADMIN", "OPERATIONS", "SUPPORT", "QUALITY", "FINANCE", "AUDITOR"]


class AdminLogin(DTO):
    login_name: Text = Field(max_length=80)
    password: str = Field(min_length=1, max_length=256)
    mfa_code: Optional[str] = Field(None, pattern=r"^\d{6}$")


class Reason(DTO):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class AdminCreate(DTO):
    login_name: Text = Field(pattern=r"^[a-zA-Z0-9_.-]{3,80}$")
    display_name: Text = Field(max_length=80)
    password: str = Field(min_length=12, max_length=128)
    role: Role


class AdminUpdate(Reason):
    status: Literal["ACTIVE", "DISABLED"]
    role: Role


class UserStatus(Reason):
    status: Literal["ACTIVE", "FROZEN"]


class EntitlementGrant(Reason):
    quantity: int = Field(ge=1, le=1000)
    ticket: str = Field("", max_length=100)


class ProductInput(Reason):
    code: Text = Field(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")
    name: Text = Field(max_length=100)
    kind: Literal["SINGLE", "MONTH", "QUARTER", "YEAR", "PACK"]
    amount_minor: int = Field(ge=1, le=100000000)
    original_minor: int = Field(ge=1, le=100000000)
    credits: int = Field(ge=1, le=10000)
    validity_days: int = Field(ge=1, le=3650)
    description: str = Field("", max_length=500)

    @model_validator(mode="after")
    def prices(self):
        if self.original_minor < self.amount_minor:
            raise ValueError("原价不能小于售价")
        return self


class PaymentInput(Reason):
    environment: Literal["sandbox", "production"] = "sandbox"
    merchant_id: Text = Field(max_length=120)
    app_id: Text = Field(max_length=120)
    secret_ref: Text = Field(pattern=r"^(kms|secrets|vault)://[a-zA-Z0-9/_.:-]{3,200}$")
    notify_url: Text = Field(pattern=r"^https://[^\s]{5,450}$")
    enabled: bool = False


class RefundInput(Reason):
    order_id: Text = Field(max_length=36)


class QualityInput(Reason):
    interview_id: Text = Field(max_length=36)
    assignee_id: Text = Field(max_length=36)


class QualityUpdate(DTO):
    status: Literal["PENDING", "IN_REVIEW", "COMPLETED", "NEEDS_FIX"]
    tags: list[
        Literal["ASR_ERROR", "REPEATED_QUESTION", "OFF_TOPIC", "NO_EVIDENCE", "REPORT_FAILED", "OTHER"]
    ] = Field(default_factory=list, max_length=6)
    conclusion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]


class ReconciliationRow(DTO):
    order_id: Text = Field(max_length=36)
    amount_minor: int = Field(ge=0, le=100000000)
    status: Literal["PAID", "REFUNDED"]


class ReconciliationInput(Reason):
    channel: Literal["WECHAT", "ALIPAY", "DEV"]
    bill_date: Text = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rows: list[ReconciliationRow] = Field(min_length=1, max_length=5000)


class MFAConfirm(DTO):
    secret: Text = Field(pattern=r"^[A-Z2-7]{32}$")
    code: Text = Field(pattern=r"^\d{6}$")
