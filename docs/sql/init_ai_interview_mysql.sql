-- AI 模拟面试服务：MySQL 8.4 首次启动基础数据
-- 先完成 ai_interview_mysql.sql 建表或 alembic upgrade head，再导入本文件。
-- 生成器：python -m scripts.export_initial_data_sql；使用方法见 docs/initial-data.md。
-- 共享模板不含凭证；首次导入前在同一连接设置 @bootstrap_admin_password_hash，
-- 并设置 @bootstrap_admin_mfa_secret_encrypted（开发环境可为 NULL）。
-- 推荐导入生成器输出的 var/bootstrap/init_ai_interview_mysql.sql，已包含所需变量。
-- 生产 MFA 密文须由与应用相同的 SECRET_KEY / ENCRYPTION_KEY 生成。
-- 按登录名、SKU 编码和邀请配置是否存在补缺；不覆盖账号密码、角色、状态或运营配置。
-- 已存在同名账号须为 ACTIVE 的 SUPER_ADMIN，才能作为新增套餐与规则的创建者。
-- 单连接执行全部语句，遇错停止并 ROLLBACK；mysql 不要使用 --force。
-- 权限由 app/modules/admin/security.py 内置，GET /api/v1/admin/roles 直接读取；无需角色表。
-- 当前角色权限树（* 表示全部权限）：
-- SUPER_ADMIN（超级管理员）
--   └─ *
-- OPERATIONS（运营管理员）
--   └─ ai:read
--   └─ dashboard:read
--   └─ growth:read
--   └─ growth:write
--   └─ interview:read
--   └─ product:read
--   └─ product:write
--   └─ quality:assign
--   └─ quality:read
--   └─ user:read
-- SUPPORT（客服人员）
--   └─ dashboard:read
--   └─ entitlement:grant
--   └─ interview:read
--   └─ report:retry
--   └─ user:read
--   └─ user:update
-- QUALITY（质检专员）
--   └─ ai:read
--   └─ interview:content
--   └─ interview:read
--   └─ media:play
--   └─ quality:read
--   └─ quality:update
-- FINANCE（财务人员）
--   └─ finance:read
--   └─ growth:read
--   └─ growth:write
--   └─ order:refund
--   └─ payment:read
--   └─ payment:write
--   └─ product:read
--   └─ product:write
--   └─ reconciliation:write
-- AUDITOR（审计安全）
--   └─ audit:read
--   └─ system:read

SET NAMES utf8mb4;

SET @bootstrap_admin_login = 'admin';

SET @bootstrap_admin_display_name = '超级管理员';

START TRANSACTION;

SET @bootstrap_now = UTC_TIMESTAMP(6);

-- 1. 初始超级管理员：使用应用相同的 scrypt 散列，不写明文密码。
INSERT INTO admin_users (
    id, created_at, updated_at, login_name, display_name, password_hash,
    `role`, status, mfa_secret_encrypted, last_mfa_step, last_login_at
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, @bootstrap_admin_login,
       @bootstrap_admin_display_name, @bootstrap_admin_password_hash,
       'SUPER_ADMIN', 'ACTIVE', @bootstrap_admin_mfa_secret_encrypted, NULL, NULL
WHERE NOT EXISTS (
    SELECT 1 FROM admin_users WHERE login_name = @bootstrap_admin_login
);

SET @bootstrap_admin_created = ROW_COUNT();

SET @bootstrap_admin_id = (
    SELECT id FROM admin_users
    WHERE login_name = @bootstrap_admin_login AND `role` = 'SUPER_ADMIN' AND status = 'ACTIVE'
);

SET @bootstrap_products_created = 0;

-- 2. 四个默认套餐：售价单位为人民币分；同 SKU 已有任何版本时跳过，不重新上架。

INSERT INTO product_skus (
    id, created_at, updated_at, code, version, name, kind, amount_minor,
    original_minor, credits, validity_days, currency, description, status,
    created_by, published_at, interview_credits
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 'SINGLE', 1,
       '单次文字训练', 'SINGLE', 990,
       990, 1, 30, 'CNY',
       '一次性发放分类面试次数；不自动续费、重置或到期回收', 'PUBLISHED', @bootstrap_admin_id,
       @bootstrap_now, JSON_OBJECT('TEXT', 1, 'VOICE', 0, 'VIDEO', 0)
WHERE NOT EXISTS (
    SELECT 1 FROM product_skus WHERE code = 'SINGLE'
);

SET @bootstrap_products_created = @bootstrap_products_created + ROW_COUNT();

INSERT INTO product_skus (
    id, created_at, updated_at, code, version, name, kind, amount_minor,
    original_minor, credits, validity_days, currency, description, status,
    created_by, published_at, interview_credits
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 'STARTER', 1,
       '多模式体验套餐', 'PACK', 3990,
       3990, 6, 30, 'CNY',
       '一次性发放分类面试次数；不自动续费、重置或到期回收', 'PUBLISHED', @bootstrap_admin_id,
       @bootstrap_now, JSON_OBJECT('TEXT', 3, 'VOICE', 2, 'VIDEO', 1)
WHERE NOT EXISTS (
    SELECT 1 FROM product_skus WHERE code = 'STARTER'
);

SET @bootstrap_products_created = @bootstrap_products_created + ROW_COUNT();

INSERT INTO product_skus (
    id, created_at, updated_at, code, version, name, kind, amount_minor,
    original_minor, credits, validity_days, currency, description, status,
    created_by, published_at, interview_credits
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 'PACK_10', 1,
       '10 次组合训练套餐', 'PACK', 6900,
       6900, 10, 30, 'CNY',
       '一次性发放分类面试次数；不自动续费、重置或到期回收', 'PUBLISHED', @bootstrap_admin_id,
       @bootstrap_now, JSON_OBJECT('TEXT', 5, 'VOICE', 3, 'VIDEO', 2)
WHERE NOT EXISTS (
    SELECT 1 FROM product_skus WHERE code = 'PACK_10'
);

SET @bootstrap_products_created = @bootstrap_products_created + ROW_COUNT();

INSERT INTO product_skus (
    id, created_at, updated_at, code, version, name, kind, amount_minor,
    original_minor, credits, validity_days, currency, description, status,
    created_by, published_at, interview_credits
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 'MONTHLY', 1,
       '20 次组合训练套餐', 'PACK', 9900,
       9900, 20, 30, 'CNY',
       '一次性发放分类面试次数；不自动续费、重置或到期回收', 'PUBLISHED', @bootstrap_admin_id,
       @bootstrap_now, JSON_OBJECT('TEXT', 10, 'VOICE', 6, 'VIDEO', 4)
WHERE NOT EXISTS (
    SELECT 1 FROM product_skus WHERE code = 'MONTHLY'
);

SET @bootstrap_products_created = @bootstrap_products_created + ROW_COUNT();

-- 3. 邀请活动：每邀请 3 名有效新用户，邀请人得 1 次文字面试；新人另得 1 次。
-- 已有任意配置版本时跳过，避免覆盖现行活动或改变其版本归因。
INSERT INTO invitation_configs (
    id, created_at, updated_at, version, enabled, threshold,
    inviter_rewards, invitee_rewards, created_by
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, 1, TRUE, 3,
       JSON_OBJECT('TEXT', 1, 'VOICE', 0, 'VIDEO', 0), JSON_OBJECT('TEXT', 1, 'VOICE', 0, 'VIDEO', 0),
       @bootstrap_admin_id
WHERE NOT EXISTS (SELECT 1 FROM invitation_configs);

SET @bootstrap_invitation_created = ROW_COUNT();

-- 4. 有新增数据时追加初始化审计；不记录密码散列、动态码或 MFA 密钥。
INSERT INTO admin_audit_logs (
    id, created_at, updated_at, admin_id, action, resource_id, reason,
    result, ip, request_id
)
SELECT UUID(), @bootstrap_now, @bootstrap_now, @bootstrap_admin_id,
       'SYSTEM_BOOTSTRAP', 'system:first-start', '首次启动初始化管理员、权益套餐和邀请配置',
       'SUCCESS', 'local', 'bootstrap-sql-v1'
WHERE @bootstrap_admin_created + @bootstrap_products_created + @bootstrap_invitation_created > 0;

COMMIT;

-- 查看实际创建数量；重复导入应全部为 0。生成的密码仅在账号首次插入时生效。
SELECT @bootstrap_admin_id AS admin_id,
       @bootstrap_admin_login AS login_name,
       @bootstrap_admin_created AS admins_created,
       @bootstrap_products_created AS products_created,
       @bootstrap_invitation_created AS invitation_configs_created;

SET @bootstrap_admin_password_hash = NULL;

SET @bootstrap_admin_mfa_secret_encrypted = NULL;
