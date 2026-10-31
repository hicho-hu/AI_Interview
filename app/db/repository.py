from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import require


class Database:
    def __init__(self, settings):
        url = settings.database_url
        backend = make_url(url).get_backend_name()
        options = {"pool_pre_ping": True}
        if backend == "mysql":
            url = make_url(url).update_query_dict({"charset": "utf8mb4"})
            options.update(
                pool_recycle=1800,
                isolation_level="READ COMMITTED",
                connect_args={
                    "connect_timeout": 10,
                    "read_timeout": 60,
                    "write_timeout": 60,
                    "init_command": "SET time_zone = '+00:00'",
                },
            )
        if backend == "sqlite":
            if url.startswith("sqlite:///") and ":memory:" not in url:
                Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if backend == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.sessions() as session:
            with session.begin():
                yield session


def owned(session, model, entity_id, user_id):
    row = session.get(model, entity_id)
    require(row is not None and row.user_id == user_id, "RESOURCE_NOT_FOUND", "资源不存在", 404)
    return row


def page_query(session, statement, page=1, page_size=20):
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return items, total
