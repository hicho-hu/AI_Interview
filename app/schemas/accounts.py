import re
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import DTO


class EmailInput(DTO):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value
        ):
            raise ValueError("请输入有效邮箱地址")
        return value


class EmailCodeInput(EmailInput):
    purpose: Literal["REGISTER", "RESET_PASSWORD"] = "REGISTER"


class EmailProof(EmailInput):
    challenge_id: str = Field(min_length=1, max_length=36)
    code: str = Field(pattern=r"^\d{6}$")


class EmailRegister(EmailProof):
    password: str = Field(min_length=10, max_length=128)
    nickname: str = Field("面试练习者", min_length=1, max_length=80)
    invite_code: Optional[str] = Field(None, max_length=24)
    promotion_code: Optional[str] = Field(None, max_length=24)
    agreed: Literal[True]


class EmailLogin(EmailInput):
    password: str = Field(min_length=1, max_length=128)


class PasswordReset(EmailProof):
    new_password: str = Field(min_length=10, max_length=128)


class EmailBinding(EmailProof):
    current_password: Optional[str] = Field(None, max_length=128)
    new_password: Optional[str] = Field(None, min_length=10, max_length=128)


class PasswordChange(DTO):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AvatarInput(DTO):
    asset_id: Optional[str] = Field(None, min_length=1, max_length=36)
    system_avatar_key: Optional[str] = Field(None, pattern=r"^avatar-(0[1-9]|1[0-6])$")

    @model_validator(mode="after")
    def avatar_source(self):
        if (self.asset_id is None) == (self.system_avatar_key is None):
            raise ValueError("请选择一张系统头像或上传头像文件")
        return self


class ContactInput(DTO):
    phone: str = Field("", max_length=24, pattern=r"^(?:\+?[0-9](?:[0-9 ()-]{5,21}[0-9])?)?$")

    @field_validator("phone")
    @classmethod
    def phone_length(cls, value):
        value = value.strip()
        if value and not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("请输入有效联系手机号")
        return value
