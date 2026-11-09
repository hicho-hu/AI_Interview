# AI 模拟面试后端

语音/视频连续面试新增：即时执行回答、SSE 流式追问、Qwen 实时字幕、24 kHz PCM 语音首包播放及整场 WebVTT 字幕保存。协议、恢复策略及配置见 [低延迟面试](docs/live-conversation.md)。

基于所提供三份 V1.0 文档实现的模块化单体，数据库按后续要求切换为 **MySQL 8.4 + Redis**。FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic；媒体使用 S3/COS 兼容私有对象存储。生产运行时 Python 3.12，本机 Python 3.9 也可运行开发测试。

## 启动

本地 dev/prod 配置分别保存在 `.env.dev` 和 `.env.prod`，选定后复制为 `.env`；两份文件包含凭证，不提交 Git。环境差异、生产待填项及切换方法见 [环境配置](docs/environments.md)。

当前本机环境可以一条命令启动（读取 `.env`，后台运行 API、Celery Worker 和 Beat）：

```bash
cd /Users/huichao/PycharmProjects/AI_Interview
.venv/bin/python -m scripts.start_local
```

脚本检查 FFmpeg/FFprobe、MySQL、Redis，未运行时尝试启动本机 MacPorts MySQL 和已安装的 Redis，自动执行 Alembic 迁移，并等待 API 就绪和 Worker 响应。启动 MySQL 可能提示输入本机管理员密码。重复执行会复用已记录且身份匹配的应用进程；日志在 `var/logs/`，PID 在 `var/run/services.json`。此脚本适用于当前 development 环境，服务在关闭终端后继续运行。

推荐完整启动（MySQL、Redis、MinIO、迁移、API、Worker、Beat）：

```bash
docker compose up -d --build
```

在本机运行 API 时，先启动数据库和 Redis，再迁移：

```bash
docker compose up -d mysql redis
.venv/bin/alembic upgrade head
TASK_MODE=eager .venv/bin/uvicorn app.main:app --reload
```

上述 eager 是本地任务执行模式，仍使用真实 MySQL/Redis；默认 worker 模式下还需启动 Celery Worker 和 Beat，Compose 会自动启动它们。

新机器使用 uv：

```bash
uv sync --locked --extra dev --python 3.12
uv run alembic upgrade head
TASK_MODE=eager uv run uvicorn app.main:app --reload
```

访问 [Swagger](http://localhost:8000/docs)、[OpenAPI](http://localhost:8000/openapi.json)、[就绪检查](http://localhost:8000/health/ready)。也可在 PyCharm 运行 `main.py`。
配置项见 `.env.example`，可手动复制为 `.env`。默认数据库连接 `mysql+pymysql://interview:interview-local@127.0.0.1:3306/interview?charset=utf8mb4`，Redis 连接 `redis://127.0.0.1:6379/0`。
本机默认 local storage/mock Provider；Compose 使用 MinIO、真实 Worker/Beat，并自动创建私有桶和执行迁移。Compose 包含公开的本地测试密码；生产使用独立环境变量和托管凭证。

MySQL 使用 InnoDB、utf8mb4、大小写敏感且保留尾部空格差异的 utf8mb4_0900_bin 排序规则；时间按 UTC 写入 DATETIME(6)，加密简历/报告使用 LONGTEXT，结构化配置使用原生 JSON。
建表 SQL：[ai_interview_mysql.sql](docs/sql/ai_interview_mysql.sql)。可导入空 MySQL 数据库，或使用 Alembic 初始化，二者选择一种。旧 PostgreSQL 文件仅作历史存档。
建表文件的 18 张表、185 个字段均包含中文 COMMENT，说明状态枚举、单位、关联和加密含义。现有库补充注释使用 [增量注释 SQL](docs/sql/update_ai_interview_mysql_comments.sql)，不要重新执行建表文件：

```bash
mysql --default-character-set=utf8mb4 -h 127.0.0.1 -P 3306 -u root AI_Interview < docs/sql/update_ai_interview_mysql_comments.sql
```

该文件针对原 f4c8a1d2e903 结构，仅更新表/字段注释，并将迁移标记同步为 3b9e7c2a4106。它保留原字段定义，不清空业务数据。也可执行 `alembic upgrade head` 完成相同更新；在线迁移会反射并保留实际字段的默认值、可空性和排序规则。
若自行修改过字段定义，可使用 `python -m scripts.export_schema_sql --kind comments --live-schema` 只读生成适合当前结构的 SQL。生成文件不代表已执行更新；本次未对现有业务库直接应用注释。
此次切换不删除或自动搬运旧 PostgreSQL/SQLite 数据；已有业务数据需另行执行数据迁移。旧 SQLite 文件仍保留在 var/ 中。

## 首次启动基础数据

建表后运行 `.venv/bin/python -m scripts.export_initial_data_sql`，生成可导入的 `var/bootstrap/init_ai_interview_mysql.sql` 和仅本地保存的管理员凭证。初始化包含超级管理员、4 个默认权益套餐、邀请奖励配置及审计，重复导入保留已有配置。角色权限由代码内置，无需额外角色表。生成、RDS 导入及 MFA 说明见 [首次启动基础数据](docs/initial-data.md)，共享模板为 [init_ai_interview_mysql.sql](docs/sql/init_ai_interview_mysql.sql)。

## 已实现的功能

|业务域|能力|
|---|---|
|用户|邮箱验证码注册、邮箱密码登录、改密与邮箱换绑、头像昵称、JWT 会话撤销、版本化授权与撤销、个人数据查询/异步删除|
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
已支持 Qwen3.8-Omni-Flash 流式文本、Qwen-Audio-3.1-TTS-Flash WebSocket 语音合成（`longanhuan_v3.1`）和 Qwen3-ASR-Flash 录音转写（`ASR_PROVIDER=qwen`、`ASR_MODEL=qwen3-asr-flash`）；北京业务空间配置及合成语音验证方法见 [Qwen 接入](docs/provider-contract.md#qwen38-omni-flash--qwen-audio-31-tts-flash)。
Compose 会从项目 `.env` 或 shell 环境读取 Provider、模型、API Key、TTS 地址与费率，统一传给 API、Worker 和 Beat。按上述 Qwen 配置保存后执行 `docker compose up -d --build` 即可应用；未配置时仍默认使用 mock。

MP3/M4A/AAC/WebM/MP4 实际校验和 ASR 音轨转换需要 FFmpeg/FFprobe，Docker 已包含。本机开发请先安装（macOS MacPorts：`sudo port install ffmpeg`；Homebrew：`brew install ffmpeg`；Debian/Ubuntu：`sudo apt-get install ffmpeg`），确保 API 和 Worker 均可执行这两个工具。也可从 [FFmpeg 下载页](https://ffmpeg.org/download.html) 获取系统对应的构建，在 `.env` 配置 `FFMPEG_BIN`、`FFPROBE_BIN` 为可执行文件的绝对路径，随后重启 API 和 Worker。本地启动脚本会先检查两项工具，缺失时立即提示；WAV 校验和文字路径无需该依赖。
生产媒体经对象存储直传；local-objects 仅是开发存储模拟端点，生产禁用。S3 校验后条件复制到 accepted/ 键，避免旧上传 URL 覆盖已接收的内容。
对象存储需配置 HTTPS、客户端 CORS、到期生命周期及私有访问；网关需能访问签名 file_url。应用每小时安排过期对象删除；本地 eager 可运行 `python -m app.workers.maintenance` 执行清理。

## 测试与交付边界

```bash
.venv/bin/python -m pytest --cov=app --cov-fail-under=80
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/alembic check
```

测试默认使用独立临时 SQLite 数据库和合成媒体，不依赖外部服务；SQLite 仅为可选测试后端。设置 `TEST_DATABASE_URL=mysql+pymysql://...` 可运行真实 MySQL 测试，每个测试自动创建并清理独立随机数据库，测试账号需具备 CREATE/DROP DATABASE 权限。CI 已切换至 MySQL 8.4。
MySQL 的真实集成测试会验证迁移往返、元数据一致性、大报告写入及区分大小写的幂等键。当前本机没有 Docker/MySQL/Redis/ffprobe，未声称完成这些组件的本机真实集成验收；已提供 Compose、CI 与离线 MySQL SQL 校验。
微信与真实 AI/云存储需要自己的凭证；本次未进行收费外部调用。真实支付、周期自动续费会员、云存储账单对账是供应商接入边界，生产不提供模拟支付。MONTHLY 目前为 20 次额度包，不含自动续费及到期扣回逻辑。
OCR、实时音视频/数字人、报告 PDF 导出、成长曲线是原文档后续增强项，未作为首版必备能力实现。
单场成本阈值和全局每日成本阈值为软预算，详见 Provider 接入说明。真实音频质量、视频真机、弱网和并发容量需在部署环境验收。

架构、ER、API 总览和任务清单见 [架构文档](docs/architecture.md)；实际完整接口与参数以 `/docs` 为准。


## 运营管理后台

新增独立管理员登录、RBAC、单人直接操作、审计、运营指标、用户与面试审阅、商品版本、财务对账等接口。启动、迁移与当前边界见 [后台运行说明](docs/admin-console.md)。

分类面试权益的套餐配置、类型余额、历史通用次数兼容和迁移说明见 [分类面试权益](docs/typed-entitlements.md)。

邮箱注册、邀请奖励与分佣推广已接入，配置和接口见 [邮箱账号与推广](docs/accounts-growth.md)。当前 Web 使用邮箱注册/密码登录；手机号与网页第三方登录暂缓。开发邮件默认预览验证码，正式部署需配置 SMTP。

## 阿里云 OSS

支持 `STORAGE_BACKEND=oss`，通过服务端 STS 按当前用户的单个待上传资产授权；Web 大文件分片上传，后端校验并复制到不可由上传凭证覆盖的 accepted 路径。角色策略、CORS、回放和切换步骤见 [OSS 接入](docs/oss-storage.md)。切换前可在保持本地存储时运行 `python -B scripts/check_oss.py --write-probe`，或在管理后台“系统管理 → 文件存储”检查连接。
