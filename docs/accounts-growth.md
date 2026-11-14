# 邮箱账号、邀请奖励与推广分佣

注册使用邮箱验证码并设置至少 10 位的密码，之后使用邮箱和密码登录。手机号与第三方登录按最新需求暂缓；联系手机号仅作为未认证的可选资料。

## 邮件配置

所有环境均可使用 `MAIL_MODE=preview`，响应带 `development_code`，页面明确显示“本地邮件预览，未发送真实邮件”。真实邮箱验证应配置：

```dotenv
MAIL_MODE=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=noreply@example.com
SMTP_USE_SSL=false
SMTP_STARTTLS=true
WEB_BASE_URL=https://your-web.example.com
```

使用 SSL 端口时设置 `SMTP_USE_SSL=true` 与对应端口。所有环境启用 SMTP 时均需配置服务器、发件地址和加密连接；预览模式由 `MAIL_MODE` 显式选择。`WEB_BASE_URL` 只用于生成邀请和推广链接，允许 HTTP/HTTPS，也可留空生成同源相对链接。邮件由服务端 SMTP 发送，浏览器不接触 SMTP 密码。不要将真实密码提交到仓库。

暂不使用邮件时可设置 `MAIL_MODE=disabled`，SMTP 参数可留空，服务仍可启动。发送注册、找回密码和绑定邮箱验证码会返回 HTTP 503 / `EMAIL_NOT_CONFIGURED`；已有账号的邮箱密码登录仍可使用。

### 生产环境临时测试注册

没有 SMTP 时，可以显式开启仅用于注册的邮件模拟能力（默认关闭）：

```dotenv
MAIL_MODE=disabled
REGISTRATION_EMAIL_MOCK_ENABLED=true
```

更新后端代码和运行环境配置并重启 API 服务；使用 Docker 时需重新创建容器以加载新环境变量。前端也需重新构建部署。注册页点击“发送验证码”后，接口返回 `delivery=registration_mock` 与 `mock_code`，页面显示“注册测试模式”、本次随机验证码和“未发送真实邮件，未验证邮箱归属”。输入该验证码后按正常流程注册，账号、密码、邀请归因及权益仍写入真实数据库，之后可用邮箱密码登录。此开关可用于 production，不需要将 `ENVIRONMENT` 改为 development。

该开关只覆盖 `REGISTER` 邮件发送；找回密码、绑定或换绑邮箱仍取决于 `MAIL_MODE`，disabled 时继续返回 503，smtp 时发送真实邮件且不返回验证码。验证码仍保持随机生成、有效期、次数限制、邮箱与用途绑定及一次性消费。测试注册不证明邮箱归属，测试期间请使用专用测试邮箱。准备启用真实邮件时设置 `REGISTRATION_EMAIL_MOCK_ENABLED=false` 和 `MAIL_MODE=smtp` 并补齐 SMTP 配置、重启服务，即可恢复真实发送。

验证码有效期 10 分钟；同一邮箱 60 秒内不可重复发送，每小时最多 5 次，来源 IP 每小时最多 20 次。验证码最多校验 5 次，与用途、目标邮箱及绑定操作账号关联，一次性消费。错误次数通过 Redis（生产必需）保留，不能通过失败请求回滚绕过。

账号将邮箱 HMAC 用于唯一查询，加密保存邮箱和联系手机号，密码保存 scrypt 散列。邮箱换绑需当前密码及新邮箱验证码；首次绑定需同时设置登录密码。改密、重置密码及换绑邮箱递增会话版本，旧令牌失效，操作响应返回新令牌。

## 主要接口

- `POST /auth/email-code`：邮箱及 `purpose=REGISTER/RESET_PASSWORD`。
- `POST /auth/email-register`：邮箱、密码、验证码、challenge_id、agreed=true；可带 invite_code / promotion_code。
- `POST /auth/email-login`：邮箱和密码。
- `POST /auth/password-reset`：邮箱验证码和新密码。
- `POST /users/me/email-code`、`POST /users/me/email`：已登录账号绑定/换绑邮箱。
- `POST /users/me/password`：当前密码和新密码。
- `PATCH /users/me/avatar`：已完成校验且归属本人的 AVATAR 资产 ID。头像支持 PNG/JPEG，限制 2 MB 与 2048×2048；Web 自动裁为 256×256 JPEG。
- `PATCH /users/me/contact`：可选联系手机号，返回 phone_verified=false。
- `GET /invitations/me`：个人邀请码、分享链接、当前规则、有效邀请和奖励次数。仅提供匿名邀请记录，不公开好友邮箱。

## 邀请奖励

后台“推广中心 → 邀请奖励配置”设置每邀请 N 人奖励一次，以及邀请人、受邀人各自的 TEXT/VOICE/VIDEO 组合。默认每 3 人奖励邀请人 1 次文字面试，受邀人注册额外获 1 次文字面试。好友必须验证邮箱并成功注册；同一个注册账号不重复计数、重复发奖。奖励直接发到分类权益余额并记账。

配置发布为新版本，仅对之后注册的好友生效。每个邀请人、配置版本和里程碑有唯一约束；注册及奖励在同一事务执行。配置关闭时保留邀请记录，不发新奖励；已发放奖励保留。

## 推广分佣

后台“推广中心 → 分佣推广链接”填写活动、推广人、佣金比例和链接状态。比例单位基点，100=1%，允许 0 至 10000。受邀新账号注册时固定推广归因，已有账号不能通过打开链接改写归因。

订单创建时固化推广链接和比例；之后修改比例不影响已创建订单。支付成功时按 `实付金额（分）× 基点 ÷ 10000` 向下取整计提，每单仅一条佣金。全额退款成功同步冲销，重复支付/退款不重复计提或冲销。台账显示已计提与已冲销，保留订单快照，不执行真实转账或提现。

停用链接停止新的注册归因，已归因账号未来订单仍按创建订单时的当前比例计算。当前真实支付仍未接入；`MOCK_PAYMENT_ENABLED=true` 时所有环境均可模拟购买和退款，走相同事务链路，不产生真实扣款。

## 迁移与验证

基础迁移版本为 `c83f5a7e0203`，系统头像选择由 `d4e6a8b10321` 增加，支付宝收款账号与线下佣金结算由 `e913c5ab7d20` 增加。使用 `alembic upgrade head` 可连续升级；手工 SQL 按现有版本依次执行 [邮箱与推广](sql/update_accounts_growth_mysql.sql)、[系统头像](sql/update_system_avatar_mysql.sql)、[推广结算](sql/update_promotion_settlement_mysql.sql)，与 Alembic 二选一。所有新表和字段均有中文数据库注释；不改变历史通用权益与已有面试记录。

后端可运行 `pytest` 与 `ruff check app tests scripts alembic`。Web 的 `scripts/verify_growth.py` 会创建临时 SQLite、预览邮箱服务和模拟支付，执行真实 HTTP/Chrome 账号与分佣流程，并可联测管理后台，结束后清理实例和临时数据。
