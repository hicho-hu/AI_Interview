# 开发初期的生产功能与任务执行

`ENVIRONMENT` 用于标识部署和缓存命名空间，不再直接禁用功能。同一份功能配置在 development、test、production 下行为一致。

| 配置 | 默认值 | 行为 |
|---|---|---|
| `TASK_MODE` | `worker` | 异步处理；`eager` 在提交请求后执行 |
| `TASK_FALLBACK_ENABLED` | `true` | API 内置执行器处理数据库任务，独立 Celery Worker/Beat 可选 |
| `TASK_POLL_SECONDS` | `1` | 内置执行器扫描间隔，单位秒 |
| `MOCK_PAYMENT_ENABLED` | `true` | 模拟购买和模拟退款可用，权益真实入账，不产生真实扣款 |
| `DEV_LOGIN_ENABLED` | `true` | 体验账号登录可用；Web 对应 `VITE_ENABLE_DEV_LOGIN` |
| `ADMIN_MFA_REQUIRED` | `false` | 不强制未启用 MFA 的管理员设置动态码；已有 MFA 验证保持生效 |
| `REDIS_REQUIRED` | `false` | Redis 暂不可用时使用本进程缓存及限流，数据库仍保存全部业务状态 |
| `MAIL_MODE` | `preview` | 所有环境允许预览验证码；也可配置 `disabled` 或 `smtp` |

真实 AI、存储及邮件仍使用各自供应商参数；选择哪种服务就配置哪种服务，不因 production 额外要求某种部署组合。资源归属、登录鉴权、验证码有效性、额度事务和订单幂等保持有效。真实支付渠道尚未接入，界面会明确显示模拟支付。

## 部署和更新

应用读取工作目录的 `.env`，沿用已有数据库和加密密钥。更新代码后重新启动 API；如果仍运行独立 Worker/Beat，也要一起重启，避免旧执行器继续领取任务。Web 仓库执行 `npm run build` 并部署新的 `dist/`。

最简后端启动方式（先完成 Alembic 迁移）：

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

默认内置执行器同时处理新任务、重试任务和已入队但没有 Worker 消费的任务。数据库原子租约保证它与 Celery 并发时只处理一次。过期面试及媒体清理也由内置执行器周期执行。

## 岗位解析恢复

Web 保留已创建的岗位。已有失败任务会原地重试一次；解析请求和轮询共用两分钟截止时间，到时取消未返回的请求并恢复操作入口，不重复新建岗位。任务成功后继续生成面试计划。

开发和生产如果共用数据库但使用不同加密密钥，执行器会跳过无法解密的任务，不消耗它们的重试次数。所有 API、Worker、Beat 应更新到相同版本；应尽量使用独立业务库隔离部署，或为共享业务数据沿用一致的密钥。

## 验证方式

后端测试包含 `ENVIRONMENT=production`、临时 SQLite、无 Redis/Celery 时的模拟购买和语音/视频准备流程，以及跨密钥任务隔离、失败岗位原地重试和显式关闭模拟支付。

Web 使用后端的虚拟环境执行：

```bash
/path/to/backend/.venv/bin/python -B scripts/verify_backend.py /path/to/backend --production
```

该验证使用临时数据库、模拟 AI 和独立端口，不读取真实简历或音视频、不调用收费供应商、不修改现有业务数据。

2026-11-10 验证结果：后端全量测试 165 通过，9 项需 MySQL 的测试在该轮跳过；MySQL/SQL 专项 20 通过，任务幂等及 ASR 的 MySQL 回归另有 17 项通过。Web 单元测试 41 通过，权益购买与创建页 14 项浏览器回归通过，production 单 API 的购买后创建语音/视频流程通过，生产构建及产物冒烟通过。Ruff、Mypy 均通过。

实际录音和摄像头访问仍受浏览器的安全上下文和权限要求约束，需要 HTTPS 或 localhost；这不由 `ENVIRONMENT` 决定。
