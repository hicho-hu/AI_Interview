# 交付验证记录

本次环境：macOS / Python 3.9.6；生产镜像声明 Python 3.12。验证于 2026-11-01 完成。

## 已执行并通过

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

本机没有 Docker、PostgreSQL 服务、Redis 服务和 ffprobe，因此未执行实际 PostgreSQL/Redis/S3/Celery 集群集成、Docker 构建、真实视频解码或 Python 3.12 运行验证。CI 提供 Python 3.12 + PostgreSQL 16 测试任务，需在 CI 运行后确认结果。
音视频测试中的 ASR/TTS 和视频探测使用固定契约替身；HTTP 兼容 Provider 和微信换码使用 HTTP 响应替身，没有使用真实凭证或收费模型调用。
未执行 20/50/100 会话容量压测、微信小程序真机录音/弱网、生产备份恢复和真实支付验收。

## 首版边界

真实支付/退款必须接入商户和验签适配器后开放；开发支付在生产被拒绝。MONTHLY 是 20 次额度包，周期会员与自动续费仍为后续增强。
OCR、报告 PDF 导出、实时语音/视频、数字人和成长曲线属于文档后续项。
对象存储直传的实际流量费用须通过云账单/访问日志对账；当前记录媒体大小与 AI Token、时长、字符及成本，不把签发 URL 当作真实流量。
内容安全提供证据校验、提示词隔离和反馈/纠错流程；专用内容审核、杀毒与供应商地区配置仍需结合生产接入选择。

完整启动与接口说明见 README；Provider 接入和软预算限制见 provider-contract.md。
