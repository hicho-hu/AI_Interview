# dev 与 prod 环境配置

项目根目录的 `.env.dev` 和 `.env.prod` 是本地配置文件，包含已有云服务凭证，已加入 Git 忽略规则，权限为 0600。应用默认读取当前工作目录的 `.env`，不会自动根据文件名切换环境。此次生成不修改现有 `.env`，也不重启运行中的服务。

| 配置 | `.env.dev` | `.env.prod` |
|---|---|---|
| ENVIRONMENT | development | production |
| 数据库 | 当前 RDS 的 AI_Interview 库 | 当前 RDS 的 AI_Interview 库 |
| Redis | 已提供的外网地址，数据库 1 | 已提供的内网地址，数据库 0 |
| 文件存储 | 当前 OSS 私有 Bucket | 当前 OSS 私有 Bucket |
| AI | 当前 Qwen 配置 | 当前 Qwen 配置 |
| 邮件 | preview，返回开发验证码 | disabled，暂不启用；使用时改为 smtp |
| 地址和 CORS | 本地前端、后台及 API | 地址可选，允许 HTTP/HTTPS；跨域时填写 CORS |
| FFmpeg/FFprobe | 当前本机工具路径 | 使用部署服务器 PATH 中的工具 |

目前只有一套 RDS/OSS 凭证，因此两份文件复用该数据库和 Bucket；它们不构成数据库和文件存储的环境隔离。若要同时运行独立的开发与生产环境，需要给开发环境另行配置数据库及 Bucket，再执行对应初始化；不要仅通过改变 `ENVIRONMENT` 区分业务数据。Redis 已按数据库 1/0 分开，避免两个环境的 Celery Worker 消费同一个默认任务队列。

## 开发环境

在项目根目录将开发配置复制为应用读取的文件：

```bash
cp .env.dev .env
.venv/bin/python -c "from app.core.config import Settings; Settings(); print('配置检查通过')"
.venv/bin/python -m scripts.start_local
```

`start_local` 会复用已经运行的服务；切换配置时，已运行的 API、Worker 和 Beat 需要先优雅停止，再启动，才能加载新配置。

## 生产环境待填项

打开 `.env.prod`，先填写其中 `TODO` 标注的配置：

- `SECRET_KEY`：原配置存在可用于生产的有效值时沿用，否则留空待填。
- `ENCRYPTION_KEY`：合法的 Fernet 密钥；用于加密业务字段和管理员 MFA。

`PUBLIC_BASE_URL` 和 `WEB_BASE_URL` 不作为生产启动的必填项，也不强制 HTTPS。它们只用于生成链接，不决定服务监听地址：前者生成本地文件访问链接，后者生成邀请和推广链接。保留默认值时使用本机开发地址；显式留空时生成相对链接，例如 `/api/v1/local-objects/...` 和 `/register?...`。

前后端部署在同一台服务器时，可由反向代理用同一个域名提供 Web，并将 `/api/` 请求转发到后端。此时两个地址可填相同的对外根地址，或留空使用同源相对链接。填入的是用户浏览器实际访问的地址，不能将服务器的 `localhost` 当作远程用户的访问地址。服务监听地址和端口由 Uvicorn 启动参数配置。

同一台服务器使用不同端口或不同域名仍属于跨域，需要在 `CORS_ORIGINS` 中填写允许访问 API 的前端和后台 Origin，用英文逗号分隔。需要对外分享完整邀请链接时，应填写实际的 `WEB_BASE_URL`；需要第三方服务访问本地文件时，应填写可被其访问的 `PUBLIC_BASE_URL`。

生产暂不使用邮件时设置 `MAIL_MODE=disabled`，所有 SMTP 配置都可留空，不阻止服务启动。发送注册、找回密码和绑定邮箱验证码的接口会返回 HTTP 503 / `EMAIL_NOT_CONFIGURED`；已有账号的邮箱密码登录仍可使用。

启用邮件时改为 `MAIL_MODE=smtp`，填写 `SMTP_HOST`、`SMTP_FROM`，以及邮件服务商要求的 `SMTP_USERNAME`、`SMTP_PASSWORD`，并开启 SSL 或 STARTTLS。默认 587 + STARTTLS；使用 465 时设置 `SMTP_USE_SSL=true`、`SMTP_STARTTLS=false`。生产环境禁止使用 `preview`。

已有业务数据时必须先确认原来的 `SECRET_KEY` 和 `ENCRYPTION_KEY`，不要直接生成新值替换：`SECRET_KEY` 还参与邮箱及身份 HMAC 查找，修改会影响已有账号登录；`ENCRYPTION_KEY` 改变后无法解密旧数据。全新部署才生成新密钥；旧部署未显式设置 `ENCRYPTION_KEY` 时，还需保留从旧 `SECRET_KEY` 派生的加密密钥。

从部署服务器检查内网 Redis 的可达性；该地址需要服务器处在可访问它的 VPC 网络中。RDS 地址沿用已提供的连接地址，未推测另一条内网地址。

填写后可以先只校验配置，不连接云服务：

```bash
.venv/bin/python -c "from app.core.config import Settings; Settings(_env_file='.env.prod'); print('生产配置检查通过')"
```

未填写必需项时，该检查会失败。配置完整后复制为服务器上的 `.env`，分别启动 API、Worker、Beat；`scripts.start_local` 仅允许 development 环境。

当前 `docker-compose.yml` 是本机 MySQL/Redis/MinIO 的开发方案，固定覆盖部分环境变量。仅使用 `docker compose --env-file .env.prod` 不能将它变为云服务生产部署。自建容器部署时须让 API、Worker、Beat、迁移进程都加载同一份生产配置，并安装 FFmpeg/FFprobe。

## 验证范围

prod 配置允许通过 `MAIL_MODE=disabled` 关闭邮件；SMTP 配置仅在启用邮件时校验。`PUBLIC_BASE_URL` 和 `WEB_BASE_URL` 不阻止启动；AI Provider 的 HTTPS 地址、云服务参数和密钥仍需满足生产约束，部署后还需验证 RDS、Redis、OSS 和 AI，启用邮件时再验证 SMTP。修改环境文件不会迁移或修改数据库、Redis、OSS 数据。
