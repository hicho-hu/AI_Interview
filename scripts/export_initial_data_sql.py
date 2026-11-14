"""Export first-start MySQL data without connecting to a database."""

import argparse
import base64
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from app.core.config import Settings
from app.core.security import Security
from app.modules.admin.security import PERMISSIONS, ROLE_LABELS, password_hash
from app.modules.billing.service import PRODUCTS
from app.schemas.admin import AdminCreate, ProductInput
from app.schemas.growth import InvitationConfigInput

SQL_FILENAME = "init_ai_interview_mysql.sql"
CREDENTIALS_FILENAME = "admin-bootstrap.json"


def sql_string(value):
    # Hex literals also work with NO_BACKSLASH_ESCAPES and cannot change SQL syntax.
    if "\\" in value or "\0" in value:
        return "CONVERT(X'" + value.encode("utf-8").hex() + "' USING utf8mb4)"
    return "'" + value.replace("'", "''") + "'"


def initial_products():
    return [
        ProductInput(
            code=code,
            **values,
            kind="SINGLE" if code == "SINGLE" else "PACK",
            original_minor=values["amount_minor"],
            validity_days=30,
            description="一次性发放分类面试次数；不自动续费、重置或到期回收",
            reason="首次启动初始化权益套餐",
        )
        for code, values in PRODUCTS.items()
    ]


def initial_invitation():
    return InvitationConfigInput(
        enabled=True,
        threshold=3,
        inviter_rewards={"TEXT": 1, "VOICE": 0, "VIDEO": 0},
        invitee_rewards={"TEXT": 1, "VOICE": 0, "VIDEO": 0},
        reason="首次启动初始化邀请规则",
    )


def json_credits(credits):
    pairs = ", ".join(f"'{mode}', {count}" for mode, count in credits.model_dump().items())
    return f"JSON_OBJECT({pairs})"


def render_initial_data_sql(login_name="admin", display_name="超级管理员", encoded_password=None, mfa=None):
    header = [
        "-- AI 模拟面试服务：MySQL 8.4 首次启动基础数据",
        "-- 先完成 ai_interview_mysql.sql 建表或 alembic upgrade head，再导入本文件。",
        "-- 生成器：python -m scripts.export_initial_data_sql；使用方法见 docs/initial-data.md。",
        "-- 共享模板不含凭证；首次导入前在同一连接设置 @bootstrap_admin_password_hash，",
        "-- 并设置 @bootstrap_admin_mfa_secret_encrypted（未启用 MFA 时可为 NULL）。",
        "-- 推荐导入生成器输出的 var/bootstrap/init_ai_interview_mysql.sql，已包含所需变量。",
        "-- 生产 MFA 密文须由与应用相同的 SECRET_KEY / ENCRYPTION_KEY 生成。",
        "-- 按登录名、SKU 编码和邀请配置是否存在补缺；不覆盖账号密码、角色、状态或运营配置。",
        "-- 已存在同名账号须为 ACTIVE 的 SUPER_ADMIN，才能作为新增套餐与规则的创建者。",
        "-- 单连接执行全部语句，遇错停止并 ROLLBACK；mysql 不要使用 --force。",
        "-- 权限由 app/modules/admin/security.py 内置，GET /api/v1/admin/roles 直接读取；无需角色表。",
        "-- 当前角色权限树（* 表示全部权限）：",
    ]
    for role, permissions in PERMISSIONS.items():
        header.append(f"-- {role}（{ROLE_LABELS[role]}）")
        header.extend(f"--   └─ {permission}" for permission in sorted(permissions))
    statements = [
        "SET NAMES utf8mb4;",
        f"SET @bootstrap_admin_login = {sql_string(login_name)};",
        f"SET @bootstrap_admin_display_name = {sql_string(display_name)};",
    ]
    if encoded_password is not None:
        statements.extend(
            [
                f"SET @bootstrap_admin_password_hash = {sql_string(encoded_password)};",
                "SET @bootstrap_admin_mfa_secret_encrypted = " + (sql_string(mfa) if mfa else "NULL") + ";",
            ]
        )
    statements.extend(
        [
            "START TRANSACTION;",
            "SET @bootstrap_now = UTC_TIMESTAMP(6);",
            """-- 1. 初始超级管理员：使用应用相同的 scrypt 散列，不写明文密码。
INSERT INTO admin_users (
    id, created_at, updated_at, login_name, display_name, password_hash,
    `role`, status, mfa_secret_encrypted, last_mfa_step, last_login_at
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, @bootstrap_admin_login,
       @bootstrap_admin_display_name, @bootstrap_admin_password_hash,
       'SUPER_ADMIN', 'ACTIVE', @bootstrap_admin_mfa_secret_encrypted, NULL, NULL
WHERE NOT EXISTS (
    SELECT 1 FROM admin_users WHERE login_name = @bootstrap_admin_login
);""",
            "SET @bootstrap_admin_created = ROW_COUNT();",
            """SET @bootstrap_admin_id = (
    SELECT id FROM admin_users
    WHERE login_name = @bootstrap_admin_login AND `role` = 'SUPER_ADMIN' AND status = 'ACTIVE'
);""",
            "SET @bootstrap_products_created = 0;",
            "-- 2. 四个默认套餐：售价单位为人民币分；同 SKU 已有任何版本时跳过，不重新上架。",
        ]
    )
    for product in initial_products():
        statements.extend(
            [
                f"""INSERT INTO product_skus (
    id, created_at, updated_at, code, version, name, kind, amount_minor,
    original_minor, credits, validity_days, currency, description, status,
    created_by, published_at, interview_credits
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, {sql_string(product.code)}, 1,
       {sql_string(product.name)}, {sql_string(product.kind)}, {product.amount_minor},
       {product.original_minor}, {product.credits}, {product.validity_days}, 'CNY',
       {sql_string(product.description)}, 'PUBLISHED', @bootstrap_admin_id,
       @bootstrap_now, {json_credits(product.interview_credits)}
WHERE NOT EXISTS (
    SELECT 1 FROM product_skus WHERE code = {sql_string(product.code)}
);""",
                "SET @bootstrap_products_created = @bootstrap_products_created + ROW_COUNT();",
            ]
        )
    invitation = initial_invitation()
    statements.extend(
        [
            f"""-- 3. 邀请活动：每邀请 {invitation.threshold} 名有效新用户，邀请人得 1 次文字面试；新人另得 1 次。
-- 已有任意配置版本时跳过，避免覆盖现行活动或改变其版本归因。
INSERT INTO invitation_configs (
    id, created_at, updated_at, version, enabled, threshold,
    inviter_rewards, invitee_rewards, created_by
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 1, TRUE, {invitation.threshold},
       {json_credits(invitation.inviter_rewards)}, {json_credits(invitation.invitee_rewards)},
       @bootstrap_admin_id
WHERE NOT EXISTS (SELECT 1 FROM invitation_configs);""",
            "SET @bootstrap_invitation_created = ROW_COUNT();",
            """-- 4. 有新增数据时追加初始化审计；不记录密码散列、动态码或 MFA 密钥。
INSERT INTO admin_audit_logs (
    id, created_at, updated_at, admin_id, action, resource_id, reason,
    result, ip, request_id
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, @bootstrap_admin_id,
       'SYSTEM_BOOTSTRAP', 'system:first-start', '首次启动初始化管理员、权益套餐和邀请配置',
       'SUCCESS', 'local', 'bootstrap-sql-v1'
WHERE @bootstrap_admin_created + @bootstrap_products_created + @bootstrap_invitation_created > 0;""",
            "COMMIT;",
            """-- 查看实际创建数量；重复导入应全部为 0。生成的密码仅在账号首次插入时生效。
SELECT @bootstrap_admin_id AS admin_id,
       @bootstrap_admin_login AS login_name,
       @bootstrap_admin_created AS admins_created,
       @bootstrap_products_created AS products_created,
       @bootstrap_invitation_created AS invitation_configs_created;""",
            "SET @bootstrap_admin_password_hash = NULL;",
            "SET @bootstrap_admin_mfa_secret_encrypted = NULL;",
        ]
    )
    return "\n".join(header) + "\n\n" + "\n\n".join(statements) + "\n"


def write_private(path, contents):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(contents)
    except BaseException:
        path.unlink()
        raise


def export_initial_data(output_dir, settings, login_name="admin", display_name="超级管理员", enable_mfa=False):
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(24)
    admin = AdminCreate(
        login_name=login_name, display_name=display_name, password=password, role="SUPER_ADMIN"
    )
    mfa_secret = (
        base64.b32encode(secrets.token_bytes(20)).decode()
        if enable_mfa or settings.admin_mfa_required
        else None
    )
    mfa_encrypted = Security(settings).encrypt(mfa_secret) if mfa_secret else None
    sql = render_initial_data_sql(admin.login_name, admin.display_name, password_hash(password), mfa_encrypted)
    output_dir = Path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    sql_path, credentials_path = output_dir / SQL_FILENAME, output_dir / CREDENTIALS_FILENAME
    credentials = {
        "login_name": admin.login_name,
        "password": password,
        "role": "SUPER_ADMIN",
        "mfa_secret": mfa_secret,
        "mfa_uri": (
            f"otpauth://totp/InterviewAdmin:{quote(admin.login_name)}"
            f"?secret={mfa_secret}&issuer=InterviewAdmin"
            if mfa_secret
            else None
        ),
        "note": "仅在 SQL 首次创建该账号时生效；生成文件不会连接或更新数据库",
    }
    write_private(credentials_path, json.dumps(credentials, ensure_ascii=False, indent=2) + "\n")
    try:
        write_private(sql_path, sql)
    except BaseException:
        credentials_path.unlink()
        raise
    return sql_path, credentials_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", action="store_true", help="Print the shared SQL template without credentials")
    parser.add_argument("--output-dir", type=Path, default=Path("var/bootstrap"))
    parser.add_argument("--login-name", default="admin")
    parser.add_argument("--display-name", default="超级管理员")
    parser.add_argument("--enable-mfa", action="store_true")
    args = parser.parse_args()
    if args.template:
        print(render_initial_data_sql(args.login_name, args.display_name), end="")
        return
    try:
        sql_path, credentials_path = export_initial_data(
            args.output_dir, Settings(), args.login_name, args.display_name, args.enable_mfa
        )
    except FileExistsError:
        parser.exit(1, "初始化文件已存在；保留原凭证，或用 --output-dir 指定新的目录。\n")
    except ValueError:
        parser.exit(2, "请检查环境配置及管理员参数：登录名须为 3 至 80 位字母、数字或 _.-，密码须为 12 至 128 位。\n")
    print(f"初始化 SQL：{sql_path}")
    print(f"管理员凭证：{credentials_path}（0600，仅本地保存）")
    print("仅生成文件，未连接或修改数据库。")


if __name__ == "__main__":
    main()
