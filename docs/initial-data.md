# 首次启动基础数据

先用 [建表 SQL](sql/ai_interview_mysql.sql) 或 `alembic upgrade head` 建立最新结构，再导入初始化数据。两份 SQL 分别负责结构和基础配置。

[初始化 SQL 模板](sql/init_ai_interview_mysql.sql) 包含超级管理员、4 个已发布权益套餐、邀请奖励规则及首次初始化审计。它只补充缺失数据，不修改已有账号的密码、MFA、角色和状态；同一 SKU 只要有任意商品版本就跳过；已有任何邀请规则版本就保留现行规则。请在启动 API/Worker 和开放流量之前，用一个数据库连接执行。

## 生成可以直接导入的 SQL

在项目根目录运行：

```bash
.venv/bin/python -m scripts.export_initial_data_sql
```

生成的两个文件都位于 Git 忽略的 `var/bootstrap/`，权限为 0600：

- `init_ai_interview_mysql.sql`：可直接导入的初始化 SQL，包含随机盐的 scrypt 密码散列。
- `admin-bootstrap.json`：登录名、随机初始密码，以及启用 MFA 时的验证器密钥和 URI。默认账号为 `admin`，角色为 `SUPER_ADMIN`。

生成器只生成文件，不连接数据库。重复运行不会覆盖已生成的文件；为另一套环境生成时，用 `--output-dir var/bootstrap-rds` 指定独立目录。`--login-name` 和 `--display-name` 可修改账号和显示名；可通过环境变量 `ADMIN_BOOTSTRAP_PASSWORD` 指定 12 至 128 位密码，勿将密码放进命令行参数或提交到仓库。

`ENVIRONMENT=production` 自动启用 MFA，其他环境可加 `--enable-mfa`。生成前须配置目标应用使用的同一套 `SECRET_KEY` / `ENCRYPTION_KEY`，否则应用无法解密 MFA。将凭证文件的 `mfa_uri` 或 `mfa_secret` 录入验证器，登录时提供六位动态码。这里的“环境”指应用的 `ENVIRONMENT`，使用 RDS 本身不会自动切换生产环境。

## 导入 RDS

先在 RDS 中创建 `AI_Interview` 数据库，并确保执行账号具备所需建表和数据读写权限；当前机器需可连接该地址。使用 MySQL 客户端交互输入 RDS 数据库密码：

```bash
# 全新空库建表；若已经通过 Alembic 建好表，跳过这一条。
mysql --default-character-set=utf8mb4 \
  -h rm-bp1oy236xwaqsr7x91o.mysql.rds.aliyuncs.com -P 3306 \
  -u YOUR_RDS_USER -p AI_Interview < docs/sql/ai_interview_mysql.sql

# 初始化基础数据。
mysql --default-character-set=utf8mb4 \
  -h rm-bp1oy236xwaqsr7x91o.mysql.rds.aliyuncs.com -P 3306 \
  -u YOUR_RDS_USER -p AI_Interview < var/bootstrap/init_ai_interview_mysql.sql
```

不要使用 `--force`；遇到错误应停止并回滚。SQL 使用事务，全部成功后提交；未完成事务的连接关闭时会回滚。如果使用数据库图形工具，需要同一连接执行所有语句，发生错误后执行 `ROLLBACK`，不要继续执行 `COMMIT`。首次导入结果应为 `admins_created=1`、`products_created=4`、`invitation_configs_created=1`，再次导入三个数量均为 0。

已有同名管理员时保留其原始凭证；本次新生成的密码不会重置这个账号。新增套餐和邀请规则要求这个账号为启用状态的超级管理员，同名账号为其他角色或被禁用时，请改用已有启用的超级管理员登录名生成 SQL。

## 默认套餐与邀请规则

套餐价格及次数与当前 `app/modules/billing/service.py` 的默认商品一致：

| SKU | 名称 | 售价（元） | 文字次数 | 语音次数 | 视频次数 |
|---|---|---:|---:|---:|---:|
| SINGLE | 单次文字训练 | 9.90 | 1 | 0 | 0 |
| STARTER | 多模式体验套餐 | 39.90 | 3 | 2 | 1 |
| PACK_10 | 10 次组合训练套餐 | 69.00 | 5 | 3 | 2 |
| MONTHLY | 20 次组合训练套餐 | 99.00 | 10 | 6 | 4 |

初始版本为 1，状态为 `PUBLISHED`，原价等于售价，币种 CNY。`SINGLE` 类型为 SINGLE，其余为 PACK；`MONTHLY` 是历史 SKU 名称。`validity_days=30` 仅为配置，当前系统一次性发放次数，不执行周期重置、自动续费或到期回收。

邀请配置初始版本为 1，活动开启，每成功邀请 3 名验证邮箱并注册的新用户，邀请人获得 1 次文字面试；受邀新人另外获得 1 次文字面试。普通注册的基础额度仍由 `.env` 的 `INITIAL_CREDITS` 控制，在注册时创建，不预建业务用户或余额。

## 角色与其他首次启动配置

角色权限由 `app/modules/admin/security.py` 的 `PERMISSIONS` 和 `ROLE_LABELS` 定义，系统没有独立角色、菜单、权限关联表。SQL 注释列出当前 6 个角色及其权限树；超级管理员登录后可调用 `GET /api/v1/admin/roles` 获取现有权限矩阵，无需插入权限表。初始化只创建一个超级管理员，其余人员账号通过后台按需创建。

真实支付配置需要商户参数和应用加密，因此不预填虚构商户记录。OSS、RDS、Redis、AI 和邮件服务在后端 `.env` 配置；`STORAGE_BACKEND=oss` 使用已有 OSS 参数。把 `DATABASE_URL` 的主机改成 RDS 后，还须填写实际 RDS 用户名和密码；密码中的特殊字符需 URL 编码。配置变更后需重启 API、Worker 和 Beat 才会生效。

共享 SQL 模板不含凭证。如果通过图形工具直接使用它，需要先在同一连接设置 `@bootstrap_admin_password_hash`（由应用的 `password_hash()` 生成）和 `@bootstrap_admin_mfa_secret_encrypted`（开发环境可为 NULL）。推荐使用生成器输出的本地完整文件。
