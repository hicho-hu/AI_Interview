"""Portable UTC timestamps and MySQL storage options."""

from datetime import timezone

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
from sqlalchemy.types import TypeDecorator

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_bin",
}
LongText = Text().with_variant(LONGTEXT(), "mysql")


class UTCDateTime(TypeDecorator):
    """MySQL stores naive UTC DATETIME(6); the application always receives aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(
            DATETIME(fsp=6) if dialect.name == "mysql" else DateTime(timezone=True)
        )

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "mysql" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
