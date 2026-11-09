"""Account and growth ORM mappings, separate from historical schema snapshots."""

from sqlalchemy.orm import registry

from app.db.growth_schema_v2 import define_tables
from app.db.models import Base

mapper_registry = registry()
names = (
    "UserAccount",
    "EmailChallenge",
    "InvitationCode",
    "InvitationConfig",
    "ReferralRegistration",
    "InvitationReward",
    "PromotionLink",
    "PromotionAttribution",
    "PromotionCommission",
)
for name, table in zip(names, define_tables(Base.metadata)):
    cls = type(name, (), {})
    mapper_registry.map_imperatively(cls, table)
    globals()[name] = cls
