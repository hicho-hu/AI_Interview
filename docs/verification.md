# 交付验证记录

本次环境：macOS / Python 3.9.6；生产镜像声明 Python 3.12。首次后端验证于 2026-11-01 完成；数据库随后按用户要求切换至 MySQL 8.4 + Redis。

## 中文字段注释交付

18 张表、185 个字段均已补充中文数据库 COMMENT，涵盖枚举、单位、关联、加密及幂等语义。全新建库 SQL 使用行内 COMMENT；现有库使用 update_ai_interview_mysql_comments.sql，或迁移到 3b9e7c2a4106，二者任选其一。
全量回归为 39 passed、6 skipped，覆盖率 89.54%；Ruff、MyPy 和 git diff --check 通过。另在本机 MySQL 8.4.13 的独立随机临时库运行专项测试，9 passed（含 6 项真实 MySQL 测试）。
实测增量 SQL 保留已有中文/emoji 数据、列类型、可空性、默认值、字符集、排序规则、字段顺序、索引和外键；重复执行、版本登记、全新 SQL 导入、迁移往返均通过。在线迁移还验证了自定义默认值和列排序规则的保留。
临时测试库已清理，现有 AI_Interview 业务库仅只读核对结构，未直接执行注释更新。注释实现参考 [MySQL ALTER TABLE](https://dev.mysql.com/doc/refman/8.4/en/alter-table.html) 与 [Alembic 操作接口](https://alembic.sqlalchemy.org/en/latest/ops.html)。

## MySQL 切换验证

默认数据库、.env.example、Docker Compose、CI、生产环境检查已改为 MySQL 8.4 + Redis，驱动改为 PyMySQL[rsa]，依赖锁已更新。
MySQL 使用 InnoDB/utf8mb4_0900_bin、UTC DATETIME(6)、LONGTEXT 和原生 JSON；新增迁移 f4c8a1d2e903 补齐循环外键。原 PostgreSQL/SQLite 数据未被修改或删除。

已验证 MySQL 方言 DDL、时间微秒与 UTC 转换、完整 SQL 与 Alembic head 一致、离线升级/回滚 SQL、SQLite 迁移往返和全部核心业务回归。实际结果为 **36 passed、4 skipped，覆盖率 89.43%**；Ruff、12 个关键源码文件的 MyPy、uv.lock 一致性和依赖完整性检查均通过。Compose 的 API/Worker/Beat/迁移连接及 MySQL 8.4 CI 配置已解析校验。
4 项真实 MySQL 专项测试（迁移往返/外键、大文本与微秒持久化、大小写及尾空格幂等、SQL 文件实际导入）已接入 MySQL 8.4 CI；本机未配置 TEST_DATABASE_URL，故明确跳过，不作为通过项。
本机未启动或连接真实 MySQL/Redis，未执行 Docker 构建；切换后的真实数据库联调须在部署环境或 CI 执行。

实现依据：[SQLAlchemy MySQL 方言文档](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)与 [MySQL 8.4 外键约束文档](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html)。历史 PostgreSQL SQL 保留为旧版本存档，当前使用 docs/sql/ai_interview_mysql.sql。

## 首次交付的历史验证记录

|检查|实际结果|
|---|---|
|Pytest 集成/业务测试|32 passed|
|整体 app 覆盖率|89.33%，达到 80% 目标|
|Ruff|All checks passed|
|MyPy（core/schemas/providers）|12 source files，无类型错误|
|Alembic upgrade|初始迁移成功，17 张业务表 + 版本表|
|Alembic round trip|独立临时库 upgrade → check → downgrade → upgrade 成功|
|Alembic check|No new upgrade operations detected|
|PostgreSQL SQL 生成|离线 SQL 生成、JSONB、循环外键单次创建检查通过|
|依赖锁|uv.lock 已生成，跨 Python 版本解析成功|
|Compose|YAML 解析成功，8 个服务；未执行 Docker 构建|
|HTTP 冒烟|临时启动 127.0.0.1:8765，完整面试到报告通过，模拟数据删除，监听进程已关闭|
|工作区检查|git diff --check 通过；.env、var、虚拟环境已忽略|

测试覆盖：状态迁移、暂停恢复、额度预占/消费/释放、并发抢额度、重复提交、越权、追问上限、定时超时、长报告分段、多版本报告、伪造证据拒绝、微信换码契约、DOCX 提取、媒体签名与格式、视频分段、ASR 低置信度、转写检查点、失败重试、重复 Worker 投递、退款幂等、过期删除、个人数据擦除、反馈/审计/管理员边界。

## 未声称通过的检查

首次交付时，本机没有 Docker、PostgreSQL 服务、Redis 服务和 ffprobe，因此未执行实际 PostgreSQL/Redis/S3/Celery 集群集成、Docker 构建、真实视频解码或 Python 3.12 运行验证。原 PostgreSQL 16 CI 已在本次切换中替换为 MySQL 8.4。
音视频测试中的 ASR/TTS 和视频探测使用固定契约替身；HTTP 兼容 Provider 和微信换码使用 HTTP 响应替身，没有使用真实凭证或收费模型调用。
未执行 20/50/100 会话容量压测、微信小程序真机录音/弱网、生产备份恢复和真实支付验收。

## 首版边界

真实支付/退款必须接入商户和验签适配器后开放；开发支付在生产被拒绝。MONTHLY 是 20 次额度包，周期会员与自动续费仍为后续增强。
OCR、报告 PDF 导出、实时语音/视频、数字人和成长曲线属于文档后续项。
对象存储直传的实际流量费用须通过云账单/访问日志对账；当前记录媒体大小与 AI Token、时长、字符及成本，不把签发 URL 当作真实流量。
内容安全提供证据校验、提示词隔离和反馈/纠错流程；专用内容审核、杀毒与供应商地区配置仍需结合生产接入选择。

完整启动与接口说明见 README；Provider 接入和软预算限制见 provider-contract.md。
