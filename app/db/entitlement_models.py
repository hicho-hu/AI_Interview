"""ORM mappings for typed interview entitlements."""

from sqlalchemy.orm import registry

from app.db.entitlement_schema_v1 import define_tables
from app.db.models import Base

mapper_registry = registry()
tables = define_tables(Base.metadata)


class CreditBalance:
    pass


class CreditAllocation:
    pass


mapper_registry.map_imperatively(CreditBalance, tables[0])
mapper_registry.map_imperatively(CreditAllocation, tables[1])
