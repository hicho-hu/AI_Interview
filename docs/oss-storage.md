# 阿里云 OSS 接入

后端的 `STORAGE_BACKEND=oss` 将头像、简历、单轮回答、整场录音录像、字幕及面试官语音保存至私有 OSS。客户端复用原上传接口，上传票据为 `OSS_STS` 时加载 `ali-oss`；8 MB 及以上使用 4 MB 分片、三片并发，上传期间刷新临时凭证。完成后仍须调用媒体确认接口，由后端检查类型、大小、内容、SHA256 及音视频时长。

## 配置与授权

长期密钥只存后端未提交的 `.env`，不要填入 Web/后台环境变量或源码。

```dotenv
STORAGE_BACKEND=oss
ALIYUN_ACCESS_KEY_ID=<RAM 用户密钥>
ALIYUN_ACCESS_KEY_SECRET=<RAM 用户密钥>
ALIYUN_RAM_ROLE_ARN=acs:ram::1250815808607228:role/ai-interview-oss-role
OSS_BUCKET_NAME=ai-interview-storage
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_REGION=cn-hangzhou
OSS_STS_ENDPOINT=sts.cn-hangzhou.aliyuncs.com
OSS_STS_DURATION_SECONDS=3600
```

在 RAM 中将 [oss-role-policy.json](oss-role-policy.json) 作为自定义权限策略绑定到角色 `ai-interview-oss-role`。这是角色的 OSS 权限，不能只给 RAM 用户绑定 `AliyunSTSAssumeRoleAccess`。RAM 用户只需能对这个角色执行 `sts:AssumeRole`，角色信任策略须允许该用户扮演。Bucket 须存在于正确账号与地域，访问权限设为私有；跨账号 Bucket 还需 Bucket Policy 授权。

`docs/oss-cors.xml` 提供本地 Web/后台的精确 Origin 及分片上传所需方法和响应头。可在 OSS 控制台「数据安全 → 跨域设置」按此填写；生产请添加实际 HTTPS 域名，协议、主机及端口必须匹配。CORS 不授予 RAM 权限。建议配置「未完成分片上传」的生命周期清理规则，避免网络中断留下的分片长期占用存储；业务文件的删除/过期仍由既有任务执行。

## 现有接口

1. `POST /api/v1/media/upload-url`（简历用 `/resumes/upload-url`）返回 `asset_id`、`method=OSS_STS` 和后端指定的 `object_key`。
2. 使用当前 JWT 请求 `GET /api/v1/storage/sts-token?asset_id=...`。服务端核验 owner、状态、有效期及隐私/设备授权，禁止指定 user_id 或任意上传路径。响应禁止缓存，仅包含短期凭证与这一个资产的上传路径。
3. 客户端将文件上传至 `ai-interview/user/{真实用户ID}/incoming/{资产ID}.{后缀}`。Session Policy 只允许该对象的写入与分片操作，不允许读取、列举、删除已接受文件或跨用户上传。
4. `POST /api/v1/media/{asset_id}/complete` 校验文件后，以源 ETag 为条件复制到 `accepted/`，再删除 incoming 对象。重用尚未过期的上传凭证无法覆盖已经接受的内容。完成接口重复调用返回同一资产。
5. 用户回放/下载、头像、简历解析和后台审阅经原授权接口获取短期签名 GET URL；后台审阅仍需支持授权、质检范围及审计，链接最长 120 秒。链接本身属于临时访问凭证，不写业务日志。

旧本地文件继续使用原签名接口，不自动搬迁。切换前请保留原 `STORAGE_PATH`。已经写入 OSS 后不要直接切回 local：应先迁移对象并更新资产路径，避免新云文件不可读。

## 验证与启用

```sh
uv sync --frozen --extra dev
.venv/bin/python -B scripts/check_oss.py
.venv/bin/python -B scripts/check_oss.py --write-probe
# 两项成功并配置浏览器 CORS 后，设置 STORAGE_BACKEND=oss。
# 已运行的 API/Worker/Beat 须先优雅停止，再用以下命令启动。
.venv/bin/python -m scripts.start_local
```

`check_oss.py` 从当前后端 `.env` 读取配置，可在 active backend 为 local 时先测试。`--write-probe` 只上传脚本生成的随机测试文本，测试完整读写、条件复制与删除，并清理这些测试对象，不上传真实用户文件。默认只检查 STS 和私有 Bucket。管理后台「系统管理 → 文件存储 → 检查 OSS 连接」可随时检查，页面不会显示密钥；此检查不代替浏览器上传、对象读写与 CORS 验收。

`OSS_AUTH_UNAVAILABLE` 表示 STS/角色扮演未通过或网络不可用；`OSS_ACCESS_DENIED` 表示当前角色无法执行 Bucket/对象操作；`OSS_BUCKET_NOT_PRIVATE` 表示 Bucket 不是私有。不要为消除错误把 Bucket 改为公共读。

参考：[浏览器 STS 上传](https://www.alibabacloud.com/help/en/oss/developer-reference/authorize-access-6)、[权限判定顺序](https://www.alibabacloud.com/help/en/oss/user-guide/authentication)、[跨域配置](https://www.alibabacloud.com/help/en/oss/developer-reference/introduction-1)。
