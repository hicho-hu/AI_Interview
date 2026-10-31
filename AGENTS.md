# 开发约定

使用模块化单体：api 只适配 HTTP；modules 的 service 编排事务与业务；db/repository 管理查询；providers 封装外部服务。
数据库通过 Alembic 迁移，当前主库为 MySQL 8.4（InnoDB/utf8mb4），SQLite 仅作可选测试后端；核心状态在关系库，Redis 只做缓存/协调。禁止上传真实简历、音视频、密钥。
面试状态迁移经统一状态机；额度、回答、报告任务和订单操作必须幂等；每个资源按 owner 鉴权。
AI 输入视为不可信数据，输出经 Pydantic 校验，评分必须提供回答证据，不推断敏感属性。
变更后运行 `pytest`、`ruff check .`，涉及数据结构时验证 Alembic upgrade/downgrade。
