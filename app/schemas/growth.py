from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.admin import ModeCredits, Reason


class InvitationConfigInput(Reason):
    enabled: bool = True
    threshold: int = Field(3, ge=1, le=10000)
    inviter_rewards: ModeCredits
    invitee_rewards: ModeCredits

    @model_validator(mode="after")
    def reward_limits(self):
        inviter = sum(self.inviter_rewards.model_dump().values())
        invitee = sum(self.invitee_rewards.model_dump().values())
        if inviter > 10000 or invitee > 10000 or (self.enabled and inviter < 1):
            raise ValueError("邀请奖励合计须为 1 至 10000 次，新人奖励可为 0 次")
        return self


class PromotionInput(Reason):
    name: str = Field(min_length=1, max_length=100)
    promoter_name: str = Field(min_length=1, max_length=100)
    alipay_account: str = Field(min_length=1, max_length=128)
    rate_bps: int = Field(ge=0, le=10000)
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"

    @field_validator("alipay_account", mode="before")
    @classmethod
    def normalize_alipay_account(cls, value):
        return value.strip() if isinstance(value, str) else value


class CommissionSettlementInput(Reason):
    link_ids: list[str] = Field(min_length=1, max_length=100)
