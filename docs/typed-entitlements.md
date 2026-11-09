# 分类面试权益

套餐通过 `interview_credits` 配置文字（TEXT）、语音（VOICE）、视频（VIDEO）次数。例如：

```json
{"TEXT": 3, "VOICE": 2, "VIDEO": 1}
```

管理后台创建商品提交上述对象，服务端计算 `credits=6`。各类型允许为 0，合计须为 1 至 10000；若同时传入 `credits`，必须等于分类次数之和。商品按现有版本发布，发布新版本仅影响之后创建的订单。默认 STARTER 体验包包含 3 次文字、2 次语音、1 次视频。

`GET /billing/entitlements` 和用户信息提供：

- `interview_credits`：各类型可用次数，不含已预占次数。
- `universal_interviews`：保留的历史通用次数。
- `remaining_interviews`：三类可用次数与通用次数之和。
- 权益接口额外返回 `reserved_interviews` 和 `reserved_by_mode`：按所创建面试的类型统计预占。

某类型可用额度为 `interview_credits[mode] + universal_interviews`，不能用其他类型余额替代。创建时先预占类型余额，再使用通用余额。开始面试将预占标为已消费；未开始取消或准备失败按原来源释放，重复操作不重复返还。准备任务重试会重新核对并预占对应类型。

订单创建时在 `order_product_snapshots` 固化类型组合。支付按快照一次性发放，重复回调不重复增加。全额退款逐类型核验余额，不使用其他类型或通用余额补足缺失的分类余额；任何类型不足则整笔退款失败并回滚，不修改订单状态或其他类型余额。

管理员补发接口 `/admin/users/{id}/entitlements` 支持 `mode=TEXT/VOICE/VIDEO/UNIVERSAL` 与 `quantity`。旧客户端未指定 mode 时仍补发通用次数。权益流水通过 `credit_allocations` 返回类型明细，历史无分类流水按通用解释。

历史兼容：保留 `users.credits` 和旧订单的通用语义，不自动转换已有余额、已预占流水、历史商品或历史订单。旧商品的 `interview_credits=null`，新建版本时可明确设置组合。账户删除会清零三类余额。

迁移：`alembic upgrade head`（版本 `b72e4f6d9102`）。迁移增加两张带中文注释的表与商品 JSON 列，不改历史余额。全量建库 SQL 与 `docs/sql/update_typed_interview_credits_mysql.sql` 已同步；增量 SQL 与 Alembic 二选一。当前仍一次性发放次数，不执行到期回收或周期重置。
