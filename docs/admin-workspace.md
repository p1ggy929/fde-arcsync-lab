# 制度与问答管理

登录原有管理员岗位（reviewer / protector / engineer / director）后，点击侧栏“制度与问答管理”，或访问 `/admin/manage`。organizer / representative 属于员工，只能访问自己的对话。原有业务岗位细分权限保持有效。

## 登录

`POST /api/login` 接收 username/password，返回 access_token、token_type、expires_in 和 access_role（employee/admin），同时设置 HttpOnly、SameSite=Strict Cookie。API 也接受 Authorization: Bearer JWT。JWT 使用 HS256，8小时有效；服务端会话支持退出立即撤销，并在每次请求检查账号是否启用及当前岗位。旧的非 JWT 登录态需重新登录。

可配置 `ARCSYNC_JWT_SECRET`（至少32字符）；未配置时首次使用生成随机密钥，持久化到业务数据库专用 jwt_keys 表，多个进程共享。更换密钥会使现有登录态失效。

写接口需 `X-ArcSync-Request: 1`；JSON 接口需 Content-Type: application/json，跨站 Origin 被拒绝。

## 制度知识库

页面：`/admin/manage?section=knowledge`。

- GET /api/admin/knowledge：列表。
- POST /api/admin/knowledge?filename=制度.md：请求体为原始文件内容（非 multipart）。限制10MB，PDF最多200页；Markdown须UTF-8，扫描PDF先做OCR。
- DELETE /api/admin/knowledge/{id}：JSON 请求体 `{}`。

按约1000字切片、150字重叠，保留PDF原始页码。最多500片。使用现有 `.env` 嵌入模型，上传时立即向量化并持久化，检索时复用缓存并按授权范围构建 FAISS。所有切片和向量成功后一次事务发布；模型异常返回503且不发布。资料默认公开供员工查阅。删除仅限此模块资料，删除对应向量、切片、文件名和文档记录，写入操作审计；旧答案引用会按现有机制判为不可用。FAISS历史快照不用于加载检索，可能保留在本地缓存目录。

## 提示词

页面：`/admin/manage?section=prompt`。GET /api/admin/prompt 获取 content/revision；PUT 同地址提交 content/revision，版本冲突返回409。初始模板见 default-system-prompt.md，仅首次初始化写入数据库，后续读取数据库模板，修改记录保留在 admin_changes。

新问答请求读取最新模板，同一次生成使用一致版本。服务端权限和引用校验不依赖提示词。已取消固定意图分类，改为自主工具选择；创建草稿前置条件由服务端强制校验。

## 对话审计

页面：`/admin/manage?section=audit`。审计现有 `/chat` 多轮问答记录（旧版仅按岗位记录的 agent_runs 不属于该人员审计页）。

GET /api/admin/conversations 支持 employee（用户ID）、start/end（含时区的ISO时间，边界包含）、offset。每页50条。PUT /api/admin/conversations/{提问消息ID}/flag 提交 flagged（布尔）、reason（最多1000字，标记时必填）。标记和取消均记录管理员及时间，保存审计变更。

审计数据、模板及人工标记存储于现有聊天SQLite数据库（ARCSYNC_CHAT_DB，默认遵循原聊天配置），支持业务库为SQLite或MySQL。疑似越权查阅／异常利用标记由管理员人工设置，不自动认定违规。

审计页同时显示工具执行记录，按员工与时间筛选参数和执行结果；校验及草稿写入另有操作审计。系统仅在数据库模板与旧默认模板完全一致时自动升级为档案模板，管理员自定义内容保留。

审计页新增Harness决策日志（轮次、工具参数、耗时、状态与结果摘要）。模型每次最多5轮，创建操作区分“提议等待确认”和“人工确认后执行”；详见[编排与分析](agent-harness.md)。
