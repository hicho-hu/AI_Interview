"""Print reproducible MySQL bootstrap and existing-schema COMMENT SQL (no database writes)."""

import argparse
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from app.db.models import Base
from app.db.schema_docs_v1 import baseline_table, comment_alter_statements

ROOT = Path(__file__).resolve().parents[1]
COMMENTS_BASE = "f4c8a1d2e903"
COMMENTS_HEAD = "3b9e7c2a4106"


def render_bootstrap_sql():
    statements = []
    dialect = mysql.dialect()

    def collect(statement):
        rendered = str(statement.compile(dialect=dialect)).strip()
        statements.append("\n".join(line.expandtabs(4).rstrip() for line in rendered.splitlines()) + ";")

    collect(CreateTable(baseline_table("alembic_version")))
    for table in Base.metadata.sorted_tables:
        collect(
            CreateTable(
                table,
                include_foreign_key_constraints=[
                    fk for fk in table.foreign_key_constraints if not fk.use_alter
                ],
            )
        )
        for index in sorted(table.indexes, key=lambda item: item.name):
            collect(CreateIndex(index))
    deferred = [
        fk for table in Base.metadata.tables.values() for fk in table.foreign_key_constraints if fk.use_alter
    ]
    for constraint in sorted(deferred, key=lambda item: item.name):
        collect(AddConstraint(constraint))
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    statements.append(f"INSERT INTO alembic_version (version_num) VALUES ('{head}');")
    header = f"""-- AI 模拟面试服务：MySQL 8.4 全新建库 SQL（含中文表注释和全部字段注释）
-- 当前结构对应 Alembic 版本 {head}，包含 {len(Base.metadata.tables)} 张业务表和 1 张迁移版本表。
-- 仅在空数据库中执行；已有数据库请使用 alembic upgrade head；不要直接执行本建库 SQL。
-- 不包含业务数据或凭证；ID、状态及时间等 ORM 客户端默认值由应用写入。
-- 示例：mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root AI_Interview < docs/sql/ai_interview_mysql.sql
-- 本文件由 scripts/export_schema_sql.py 生成；MySQL DDL 会隐式提交。
"""
    return header + "\nSET NAMES utf8mb4;\n\n" + "\n\n".join(statements) + "\n"


def render_comments_sql(connection=None):
    statements = comment_alter_statements(connection)
    statements.append(
        f"UPDATE alembic_version SET version_num='{COMMENTS_HEAD}' WHERE version_num='{COMMENTS_BASE}';"
    )
    header = f"""-- AI 模拟面试服务：现有数据库字段/表注释增量更新 SQL
-- 适用结构：Alembic {COMMENTS_BASE}；执行后登记为 {COMMENTS_HEAD}。
-- 仅修改 COMMENT，不删除表、不改变业务记录，不改字段类型、可空性、默认值、索引或外键。
-- MODIFY COLUMN 需要完整字段定义，本文件保留原结构定义；不会使用业务数据 UPDATE。
-- 最后一条 UPDATE 仅同步 Alembic 迁移版本标记；同版本可重复执行。
-- 示例：mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root AI_Interview < docs/sql/update_ai_interview_mysql_comments.sql
-- 若额外自定义过字段类型/默认值，使用 alembic upgrade head（自动反射保留），或 --live-schema 重新生成。
-- 与 alembic upgrade head 选择一种执行；MySQL DDL 隐式提交，不保证整份脚本可整体回滚。
"""
    return header + "\nSET NAMES utf8mb4;\n\n" + "\n\n".join(statements) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["bootstrap", "comments"], default="bootstrap")
    parser.add_argument("--json", action="store_true", help="Print both artifacts as one JSON object")
    parser.add_argument(
        "--live-schema", action="store_true", help="Read .env database definitions without updating it"
    )
    args = parser.parse_args()
    if args.live_schema:
        from app.core.config import Settings
        from app.db.repository import Database

        db = Database(Settings())
        try:
            with db.engine.connect() as connection:
                comments = render_comments_sql(connection)
        finally:
            db.engine.dispose()
    else:
        comments = render_comments_sql()
    artifacts = {"bootstrap": render_bootstrap_sql(), "comments": comments}
    print(
        json.dumps(artifacts, ensure_ascii=False) if args.json else artifacts[args.kind],
        end="\n" if args.json else "",
    )


if __name__ == "__main__":
    main()
