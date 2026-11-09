-- Upgrade accounts/growth schema from d4e6a8b10321 to e913c5ab7d20.
-- Apply once when using the manual MySQL schema workflow instead of Alembic.
ALTER TABLE promotion_links
    ADD COLUMN alipay_account_encrypted LONGTEXT NULL COMMENT '线下打款用支付宝账号（加密存储）';

ALTER TABLE promotion_commissions
    ADD COLUMN settled_at DATETIME(6) NULL COMMENT '线下打款结算时间（UTC）',
    MODIFY COLUMN status VARCHAR(16) NOT NULL COMMENT '应计状态：ACCRUED=已计提，REVERSED=退款已冲销，SETTLED=已线下打款';

UPDATE alembic_version
SET version_num = 'e913c5ab7d20'
WHERE version_num = 'd4e6a8b10321';
