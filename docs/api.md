# JSON API

FastAPI 同源根路径 `/api`。health、login、接口文档和 OpenAPI 定义可公开访问，其他接口均需登录。所有 POST 需要：

```http
Content-Type: application/json
X-ArcSync-Request: 1
```

登录成功返回 HttpOnly 会话 Cookie，浏览器自动携带。POST 跨源 Origin 被拒绝。

## 读取

| 方法与路径 | 返回 |
| --- | --- |
| GET /api/health | 服务状态、演示模式、未接入真实系统标志 |
| GET /api/session | 当前用户、岗位、portal、home |
| GET /api/admin/overview | 仅 B 端账号；业务数、未处理异常、待审核数、暂停状态 |
| GET /api/search/stream?q=&category=&year=&status= | 鉴权后的 SSE 检索结果流 |
| GET /api/openapi.json | 自动生成的接口定义 |
| GET /api/docs | 离线接口说明入口 |
| GET /api/state | 按权限投影的目录、业务、事件、交接、异常、反馈、配置、审计 |
| GET /api/search?q=&category=&year=&status= | 授权范围内的匹配目录；关键词按空格拆分，所有词均须命中 |
| GET /api/documents/{id}?page=6 | 指定扫描页、纸质页、资料版本与合成正文；逐次鉴权 |

## 会话

`POST /api/login`：`{"username":"organizer","password":"ArcSync2026!"}`（旧版 `role` 字段仍兼容）。返回 `role/name/label/portal/home`，由服务端决定工作端；请求中传入 `portal` 不影响权限。。

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

## SSE 流式检索

前端已使用 `/api/search/stream`。同源 Cookie 鉴权，返回 `Content-Type: text/event-stream`、`X-Accel-Buffering: no`、`Cache-Control: no-store`。格式如下：

```text
event: start
data: {"count":1,"source":"合成目录与固定摘要；未接入 AI 或真实 OCR"}

event: document
data: {"id":"YZ-CJ-1992-008","title":"示例标题，其余字段同 /api/search"}

event: done
data: {"count":1}

```

document 可出现零次或多次。握手前鉴权失败返回 HTTP 401；握手成功后会话失效发送 `event: expired`，data 为 `{"error":"会话已失效，请重新登录"}`，随后结束。每条记录发送前复查会话；不持有长数据库事务。浏览器重检、切页、退出均取消前次请求；无 done 即中断，需用户重试，不自动重复返回结果。反向代理需关闭该路径的缓冲。

并发采用 Uvicorn + FastAPI 线程池处理同步 SQLite 操作，SSE 异步发送；SQLite WAL 与 revision 写冲突返回 409，当前没有生产吞吐保证。

## 智能体与数据模型 API

| 路径 | 权限及输入 / 输出 |
| --- | --- |
| GET /api/models/status | 已登录；只返回配置状态、提供方/模型名、FAISS、数据库种类，不返回密钥和端点地址 |
| POST /api/admin/models/check | 工程师或保护专员，JSON `{}`；实际请求一次对话及嵌入，返回维度与本次延迟；未配置/失败503 |
| POST /api/admin/knowledge/documents | 工程师/保护专员；受限资料仅保护专员；版本化导入，输入见下例 |
| POST /api/agent/chat/stream | 已登录，JSON `{"question":"查县城规划与后续调整"}`；同源与会话鉴权；SSE 事件流 |
| GET /api/agent/runs | 当前岗位最近20次运行概况 |
| GET /api/agent/runs/{id} | 当前岗位自己的运行、来源、步骤及用量；权限/资料版本变化则拒绝返回旧回答 |

导入示例：

```json
{"id":"KS-2026-001","title":"规划说明","category":"城市建设","year":2026,"level":"公开","version":"v1","pages":[{"scan_page":1,"paper_page":11,"text":"人工整理的页文本。"}]}
```

更新须使用新版本并带 `expected_version`。每次一个版本1–20页，JSON总计最多32 KiB，每页最多5000字；同一版本禁止覆盖和追加，旧文本保留。

智能体流事件：`start`（run_id）→ `step` / `intent` / `tool`（可多次）→ `sources` → `token`（草稿）→ `done`（最终辅助回答、来源、usage）。失败发送 `error`，包括 `discard_draft:true`，客户端应清除草稿。握手前配置错误503、暂停409、繁忙429；同岗位最多一个运行，单进程最多四个。停止或断连取消上游请求并保存 cancelled。没有自动重试或后台重放。

`done` 的引用校验检查来源编号、授权、当前版本与内容；事实正确性和原卷对应仍需人工审查。usage 只累计提供方返回的对话 token，不代表全部费用。工具清单与第三、四层边界见架构说明。
