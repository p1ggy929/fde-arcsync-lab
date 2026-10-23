# JSON API

同源根路径 `/api`。除 health、login 外均需登录。所有 POST 需要：

```http
Content-Type: application/json
X-ArcSync-Request: 1
```

登录成功返回 HttpOnly 会话 Cookie，浏览器自动携带。POST 跨源 Origin 被拒绝。

## 读取

| 方法与路径 | 返回 |
| --- | --- |
| GET /api/health | 服务状态、演示模式、未接入真实系统标志 |
| GET /api/session | 当前用户、岗位 |
| GET /api/state | 按权限投影的目录、业务、事件、交接、异常、反馈、配置、审计 |
| GET /api/search?q=&category=&year=&status= | 授权范围内的匹配目录；关键词按空格拆分，所有词均须命中 |
| GET /api/documents/{id}?page=6 | 指定扫描页、纸质页、资料版本与合成正文；逐次鉴权 |

## 会话

`POST /api/login`：`{"role":"organizer","password":"ArcSync2026!"}`。

`POST /api/logout`：`{}`。

## 操作

路径均为 `POST /api/actions/{action}`。涉及业务修改的操作需带 `business_id` 与当前 `revision`；accept、resolve、reply 使用独立记录 id。create、pause、sync 不带业务修订号。

| action | 必要字段 | 权限与条件 |
| --- | --- | --- |
| create | title, applicant, purpose | 整理员、审核员、查询代表；创建后由审核员核验 |
| authorize | business_id, revision, scope="公开档案试点" | 当前负责的审核员，创建交整理员的待接手记录 |
| note | business_id, revision, note | 当前责任岗位；不可修改已交付记录 |
| cite | business_id, revision, document_id, version, scan_page, paper_page, correction_checked | 当前负责的整理员、定位中、有效授权版本；有更正时 correction_checked 必须为 true |
| submit | business_id, revision, note | 整理员，已有确认引用、无未解决异常；创建待审核与待接手记录 |
| handoff | business_id, revision, receiver, note | 当前责任岗位；按节点限制接手岗位；不可有重复待接手记录 |
| accept | id | 指定接手岗位；确认后转移业务责任 |
| approve | business_id, revision, note | 当前负责的审核员；复查引用与未解决异常 |
| return | business_id, revision, reason | 当前负责的审核员；清空旧引用，保留退回原因并交整理员接手 |
| deliver | business_id, revision, note | 当前负责的审核员，须已审核通过 |
| issue | business_id, revision, document_id(可空), kind, detail | 整理员、审核员、工程师、保护专员；暂停建议转人工，关联资料须公开 |
| assign | business_id, revision, id, receiver, note | 审核员；指派实际处理岗位 |
| resolve | id, resolution | 被指派岗位；只保存处理结果，不修改源资料 |
| resume | business_id, revision, note | 审核员复核，已无未解决异常；交整理员接手 |
| feedback | business_id, version, content | 查询代表、整理员、审核员；登记待核对反馈 |
| reply | id, reply | 审核员核对反馈 |
| pause | paused(布尔), reason | 主任/保护专员暂停，仅保护专员恢复 |
| sync | 无 | 工程师；只刷新本地合成快照 |

写入成功：`{"ok":true}`；create 额外返回 `id`。

错误：`{"error":"中文原因"}`，状态码 400 参数无效、401 未登录或会话过期、403 权限不足、404 不存在或不可见、409 状态或版本冲突、413 内容过大、415 内容类型不符。

并发写请求在事务中校验 revision，失败操作回滚。API 不提供真实授权写入、原档案编辑或删除功能。
