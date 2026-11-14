# 运营管理后台

独立 Web 入口位于 `/Users/huichao/WebProjects/platform_AI_Interview`，Vite 开发端口 5174。所有新接口使用 `/api/v1/admin`，API 文档位于 `/docs`。普通用户 token 和原 `ADMIN_USER_IDS` 白名单不再授予后台权限；遗留统计、反馈回复、调用日志接口也已切换到独立管理员认证，历史业务审计改为 `/admin/business-audit-logs`。

执行 `.venv/bin/python -m alembic upgrade head` 增量新增 12 张管理表，保留原有业务数据；每张新表及字段均提供中文 COMMENT，历史注释快照不改写。迁移版本 `a61d8c4e9201`，基线 `3b9e7c2a4106`。增量 SQL 为 `docs/sql/update_admin_console_mysql.sql`，与 Alembic 二选一。

执行 `.venv/bin/python -m scripts.admin_cli` 创建初始管理员，默认登录名 admin，随机密码写入 `var/admin-bootstrap.json`（0600）。第一期只需一个有相应操作权限的管理员。设置 `ADMIN_BOOTSTRAP_PASSWORD` 可指定密码，至少 12 位。所有环境均通过 `ADMIN_MFA_REQUIRED=true` 或 `--enable-mfa` 启用 TOTP；已启用 MFA 的账号始终需要有效动态码，使用验证器录入生成文件中的 `mfa_secret`。

六类角色为 SUPER_ADMIN、OPERATIONS、SUPPORT、QUALITY、FINANCE、AUDITOR。权限矩阵定义于 `app/modules/admin/security.py`，没有后台 UI 可以绕过的角色检查。管理员会话 30 分钟过期；禁用或变更角色会撤销会话。密码使用 scrypt，TOTP 防重放；敏感配置和幂等响应使用现有加密设施。

第一期取消双人审批：商品草稿发布、支付配置保存、额度补发和退款由有权限的管理员直接执行，保留操作原因、幂等和审计。补发工单号选填。旧审批 API 和角色审批权限已移除，历史审批记录保留且不会自动执行；旧待发布商品可直接发布，旧待退款事项可在订单页重新执行。管理员写操作提供 `Idempotency-Key`，同键异参返回 409。

媒体/文本审阅要求 `SUPPORT_REVIEW` 当前政策版本授权；质检员另须有效的分派任务。后台不提供代签同意接口。超出保留期限、已删除面试、过期媒体不可审阅，签名 URL 最长 120 秒。签名 URL、转写、私钥不进入管理审计正文。浏览器播放器内存保存播放 URL，到期移除。

## 明确尚未接入的生产能力

支付配置直接保存，真实微信/支付宝收款、服务端回调验签、KMS 密钥解引用、真实退款及自动拉取账单未实现；真实支付渠道不可启用，真实退款返回 503。配置开启的模拟购买与退款在所有环境可用，不产生真实扣款。对账支持导入明确渠道前缀的订单账单与差异处理，不伪造自动拉单。

月/季/年商品为一次性固定额度套餐，暂未执行有效期回收、会员周期重置、自动续费。AI 模型配置只读，在线路由发布、外部告警、异步大批量导出及正式压测待实现。运营 DAU 当前以创建面试用户定义，不具备访客及报告浏览埋点，漏斗只展示实际采集阶段。
