-- 系统头像选择增量迁移，适用于 c83f5a7e0203；与 Alembic 二选一。
-- 若旧版手工建库已含 system_avatar_key，请先核对字段，避免重复添加。
ALTER TABLE user_accounts
    ADD COLUMN system_avatar_key VARCHAR(16) NULL COMMENT '用户选择的系统头像目录标识';

UPDATE alembic_version
SET version_num = 'd4e6a8b10321'
WHERE version_num = 'c83f5a7e0203';
