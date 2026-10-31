# AI 模拟面试后端

基于所提供三份 V1.0 文档实现的模块化单体。FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic；生产使用 PostgreSQL、Redis/Celery、S3/COS 兼容私有对象存储。生产运行时 Python 3.12，本机 Python 3.9 也可运行开发测试。

## 启动

当前工作区 `.venv` 已安装开发依赖，首次运行：

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

新机器使用 uv：

```bash
uv sync --locked --extra dev --python 3.12
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

访问 [Swagger](http://localhost:8000/docs)、[OpenAPI](http://localhost:8000/openapi.json)、[就绪检查](http://localhost:8000/health/ready)。也可在 PyCharm 运行 `main.py`。
配置项见 `.env.example`，可手动复制为 `.env`。默认 SQLite/local storage/eager/mock，无需外部服务。

完整基础设施：

```bash
docker compose up --build
```

Compose 会启动 PostgreSQL、Redis、MinIO，创建私有桶、执行迁移，然后启动 API、Worker、Beat。Compose 为本地开发配置，包含公开的本地测试密码；生产使用独立环境变量和托管凭证。

## 已实现的功能

|业务域|能力|
|---|---|
|用户|微信 code 换码、JWT、退出撤销、用户资料、版本化授权与撤销、个人数据查询/异步删除|
|岗位/简历|JD 录入/结构化、级别、岗位管理；PDF/DOCX 私有上传/文字提取/结构化，计划结合简历事实|
|面试|TEXT/VOICE/VIDEO；15/30/45 分钟、难度/语言/题数/类型；计划、首题、暂停/恢复/取消、超时/题数结束|
|动态问答|独立引擎；FOLLOW_UP/NEXT_QUESTION/CLARIFY/FINISH；追问/澄清上限、能力覆盖、最近轮次摘要|
|语音/视频|TTS、ASR、低质量转写澄清、转写检查点、签名直传、实际媒体校验、分段轮次关联、签名回放、到期删除|
|报告|六维评分、逐题证据/优缺点/建议/参考思路、训练建议和练习题；长会话分段；快照、多版本重生成、历史筛选|
|用量/商业|免费额度、原子预占/消费/释放、用量账本、单次/套餐/月度额度包订单、开发支付/全额退款、AI 调用和成本|
|运维|Outbox、任务租约/重试/幂等、审计、反馈/纠错、管理员统计、限流、供应商熔断、健康检查、CI|

所有资源按用户归属检查；JD/简历/问题/转写/报告加密；重要写入使用事务；面试状态使用版本乐观锁；创建/提交/结束/报告重生成/订单退款有幂等键和请求哈希检查。

## 前端对接顺序

1. 开发环境 `POST /api/v1/auth/dev-login`：`{"identity":"your-dev-id"}`，保存 access_token。真实微信使用 `/auth/wechat-login`，传 `{"code":"小程序wx.login返回的code"}`。
2. `POST /users/me/consents` 分别授予 `PRIVACY`、`TERMS`、`AI_PROCESSING`，policy_version 为 `1.0`。音视频还需 `MICROPHONE`，视频还需 `CAMERA`。
3. `POST /job-profiles` 传 job_title/jd_text/seniority。轮询 task_id 或岗位状态到 READY。简历可选：申请 `/resumes/upload-url` → PUT 原始文件到签名 URL → `/media/{asset_id}/complete` → 等待简历 READY。
4. `POST /interviews`，传 job_profile_id、可选 resume_asset_id、config；提供 `Idempotency-Key`。轮询会话到 READY，再调用 `/interviews/{id}/start`。
5. 读取 current_question；文字回答 `/interviews/{id}/turns/{turn_id}/submit`：`{"text":"回答"}`。语音/视频先申请 `/media/upload-url`，绑定 interview_id/turn_id、size、duration_ms，直传并 complete，再提交 `{"asset_id":"...","client_duration_ms":1000}`。
6. 轮询 `/tasks/{task_id}` 后刷新会话；网络恢复时 GET 会话/轮次即可恢复。失败任务可显式 `/tasks/{id}/retry`。已提交答案不会要求客户端重传。
7. 用户结束调用 `/interviews/{id}/finish`：`{"reason":"USER"}`；自动结束无须额外调用。轮询报告；GET report 始终读取已保存结果。

提交回答、创建面试、结束、报告重生成、创建订单、支付和退款必须带 `Idempotency-Key`；同键同请求返回原结果，同键异请求返回 409。并发冲突 409 可用原键重试。
暂停不计面试时间。主问题数量不含追问和澄清；拒绝权限时可选择 TEXT，语音轮次也可直接提交文字。
视频各段用同一 turn_id 上传；GET `/media?interview_id=...&turn_id=...` 可按上传顺序获得回放列表。提交 `asset_ids` 有序数组可聚合最多 3 段回答，累计最长 3 分钟，后端依次转写并统一分析。
单段音频最长 3 分钟，视频最长 2 分钟；音视频每段最大 32MB；PDF/DOCX 最大 10MB。

## AI 与媒体运行说明

默认 mock 用于开发演示：不会识别真实录音、不会输出真实语音，也不会给出虚构的能力分数。报告 `is_mock=true`，分数为 null。
配置 `PROVIDER_MODE=compatible` 可直接连接 Chat Completions / ASR / TTS 兼容端点；也可使用 `http` 自有网关。配置、成本单位和协议见 [Provider 接入说明](docs/provider-contract.md)。

MP3/M4A/AAC/WebM/MP4 实际校验、视频音轨提取需要 ffmpeg/ffprobe，Docker 已包含。未安装时返回明确错误，WAV 和文字路径无需该依赖。
生产媒体经对象存储直传；local-objects 仅是开发存储模拟端点，生产禁用。S3 校验后条件复制到 accepted/ 键，避免旧上传 URL 覆盖已接收的内容。
对象存储需配置 HTTPS、客户端 CORS、到期生命周期及私有访问；网关需能访问签名 file_url。应用每小时安排过期对象删除；本地 eager 可运行 `python -m app.workers.maintenance` 执行清理。

## 测试与交付边界

```bash
.venv/bin/python -m pytest --cov=app --cov-fail-under=80
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/alembic check
```

测试用独立临时数据库和合成媒体。可设置 TEST_DATABASE_URL 运行 PostgreSQL 测试，每次测试创建独立 schema；CI 已配置 PostgreSQL 16。
本机没有 Docker/PostgreSQL/Redis/ffprobe，不能声称在本机完成了这些组件的真实集成验收；已提供 Compose、CI 与离线 PostgreSQL 迁移 SQL 校验。
微信与真实 AI/云存储需要自己的凭证；本次未进行收费外部调用。真实支付、周期自动续费会员、云存储账单对账是供应商接入边界，生产不提供模拟支付。MONTHLY 目前为 20 次额度包，不含自动续费及到期扣回逻辑。
OCR、实时音视频/数字人、报告 PDF 导出、成长曲线是原文档后续增强项，未作为首版必备能力实现。
单场成本阈值和全局每日成本阈值为软预算，详见 Provider 接入说明。真实音频质量、视频真机、弱网和并发容量需在部署环境验收。

架构、ER、API 总览和任务清单见 [架构文档](docs/architecture.md)；实际完整接口与参数以 `/docs` 为准。
