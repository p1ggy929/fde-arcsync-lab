# 多轮档案智能查阅接口与使用说明

页面入口：`/chat-ui`，或在员工门户/管理后台侧栏点击“智能查阅”。页面使用HTML与原生JavaScript，不依赖外部CDN。

## 接口

### POST /chat

登录Cookie自动携带；当前认证同时兼容仓库现有Bearer鉴权。POST须携带 `Content-Type: application/json` 与 `X-ArcSync-Request: 1`，跨站Origin被拒绝。

```json
{"session_id":null,"question":"员工申请年假需要提前几天？"}
```

两个字段都必须提供。不接受客户端传入user_id、历史、模型地址等额外字段。question必须为字符串，去除首尾空白后1～2000字；session_id首次为null，继续问答传返回的UUID。会话ID不是登录凭据，不存在或无权访问统一404。

建立SSE前完成认证、参数、会话归属、模型配置、暂停状态及并发/频率检查；此阶段出错返回HTTP错误状态和JSON：

```json
{"code":40101,"message":"请先登录或重新登录","data":null,"error":"请先登录或重新登录"}
```

error为旧页面兼容别名，新客户端读取code/message/data。框架产生的参数错误、404、405和未预期错误也有中文说明。

| HTTP状态 | 业务码 | 说明 |
| --- | --- | --- |
| 400 | 40001 | 参数或问题格式无效 |
| 401 | 40101 | 未登录、身份失效 |
| 403 | 40301 | 请求来源或权限校验失败 |
| 404 | 40401 | 会话不存在或不可见 |
| 405 | 40501 | 不支持的请求方法 |
| 409 | 40901 | 同会话/用户已有生成，或试点已暂停 |
| 413 | 41301 | JSON请求体超过32 KiB |
| 415 | 41501 | 请求体不是JSON |
| 429 | 42901 | 频率超限或服务并发繁忙 |
| 503 | 50301 | 模型尚未配置 |
| 500 | 50001 | 处理失败，不公开内部异常 |

SSE响应为 `text/event-stream`，关闭缓存及代理缓冲。事件间用空行分隔，所有事件的data均为JSON，code=0表示正常：

```text
event: session
data: {"code":0,"message":"会话已就绪","data":{"session_id":"UUID","message_id":"UUID"}}

event: status
data: {"code":0,"message":"正在检索制度…","data":{"stage":"retrieving"}}

event: status
data: {"code":0,"message":"正在生成回答…","data":{"stage":"generating"}}

event: delta
data: {"code":0,"message":"回答生成中","data":{"text":"根据制度原文……[C1]"}}

event: citations
data: {"code":0,"message":"引用来源已就绪","data":{"items":[{"id":"C1","document_id":"POLICY-001","filename":"员工休假管理办法.pdf","version":"v1","section":null,"page":2,"snippet":"员工申请年假应……"}]}}

event: done
data: {"code":0,"message":"回答已完成并保存","data":{"session_id":"UUID","message_id":"UUID","status":"completed","answer":"根据制度原文……[C1]"}}

```

status/delta可出现多次。中间可发送 `: heartbeat` 注释，客户端忽略。citations在回答通过既有来源校验并写库后发送；done.answer为完整、已保存的标准文本，客户端用它核对打字队列，避免丢字或重复。

已经开始SSE后不能更改HTTP状态，生成失败使用终止事件，之后不再发送done：

```text
event: error
data: {"code":50302,"message":"模型连接中断，请重新提问","data":{"session_id":"UUID","message_id":"UUID","discard_draft":true}}

```

流内50302表示生成中断、超时或模型调用错误；40402表示未检索到有效依据；40101/40901表示中途身份或状态变化。客户端应撤回草稿，保留问题。停止/断连会保存cancelled；连接已关闭时不能保证再向客户端发送错误事件，状态可通过历史读取。

### GET /chat/sessions

参数：limit默认20，范围1～50；offset默认0。返回当前独立用户的会话，按更新时间倒序。

```json
{"code":0,"message":"会话列表读取成功","data":{"sessions":[{"id":"UUID","title":"第一条问题","created_at":"UTC ISO时间","updated_at":"UTC ISO时间"}],"has_more":false,"next_offset":null}}
```

### GET /chat/sessions/{session_id}/messages

参数：limit默认20，指20轮、最多40条消息；范围1～50。before_turn可选，用于向前加载更早轮次。

返回code/message/data，data包含session、messages、has_more、next_before_turn。消息包括id、turn_no、role、content、status、citations、error_code、error_message和时间。分页返回的消息按轮次升序、问题在回答前排列。

来源或访问权限变化时，历史回答返回unavailable占位，正文和引用片段清空；数据库原记录保留。模型未配置时仍可读取自己的历史。

## SQLite与身份

新增chat_sessions和chat_messages两张聊天表，不混用登录sessions表。SQLite业务模式默认复用原ARCSYNC_DB文件；MySQL业务模式默认使用data/chat.sqlite3保存聊天。可以通过ARCSYNC_CHAT_DB显式指定SQLite文件，空值等同于未指定。

如从SQLite业务库切换MySQL，请将ARCSYNC_CHAT_DB明确设置为原聊天SQLite文件路径，避免切换默认路径后误以为旧聊天消失。MySQL迁移工具复制具名账号ID和文件名元数据，不搬迁这两张聊天表。

user_accounts为独立账号表，user_id与岗位分离；session_identities将原登录会话映射到人员。旧账号及其密码会兼容迁移，升级后建议重新登录。共享演示账号仍然代表同一个账号，两个人共用它就会共用聊天；要实现人员隔离必须使用不同用户名。

本机创建独立员工账号：

```sh
.venv/bin/python scripts/create_user.py --username employee.zhang --name 张同事 --role organizer
```

密码在终端交互输入两次，至少12位，不进入命令行参数。不提供可自行选择管理权限的公网注册接口。只有账号可用时，其Cookie/Bearer才能访问聊天；停用账号后原登录也失效。

资料原始文件名记录于document_files，可在导入页文本API中传filename；未提供文件名的既有资料使用资料名称，不伪造PDF名称。条款或页码没有可靠数据时为null。聊天引用片段最长500字，回答和制度原文仍须人工核验。

## 上下文与事务

- 仅从本用户会话中读取本次之前最近6组completed问答，最多12条消息；失败、取消和生成中记录不参与。
- 将每组历史的来源按当前权限、版本与内容复查，失效轮次跳过。历史只用于理解追问，不充当本轮证据，旧引用编号不能复用。
- 历史使用保守的12000字符预算，超过时按整轮移除最早记录；不是对所有模型精确相同的token预算。
- 历史用于自主工具决策和最终生成；档案事实仍需本轮实时授权来源。
- 问题与回答占位一次短事务写入，回答最终完成再短事务更新；模型请求和SSE发送期间不持有SQLite写事务。
- 部分唯一索引阻止同会话并发生成；事务检查同账号最多一条生成。每账号每分钟最多12次已接受请求。进程内与旧智能体共用4任务上限；该全局上限不跨实例共享，不代表已完成公网统一配额。
- 整个任务最多180秒；占位租约为任务时限加30秒。进程崩溃后租约到期自动标记failed，避免永久占用。正常断连会取消上游任务并及时记为cancelled。
- 新聊天不写入按岗位共享的旧agent_runs，避免经旧历史接口暴露新聊天内容。旧业务接口及历史运行的其他公网安全问题仍按安全报告整改，本次不能替代全面上线加固。

## 界面与验证

用户可以新建会话、继续追问、查看最近会话、加载更早消息、刷新恢复、停止生成。原生fetch POST读取SSE，独立队列实现逐字显示；模型正文和制度片段使用textContent展示，避免执行返回的HTML。网络和解析异常也转成中文提示。

自动测试覆盖身份兼容、同岗位不同用户隔离、6轮上下文、各阶段模型请求包含历史、事务并发、频率限制、超时/取消、来源变化与SQLite持久化。

浏览器联调使用独立临时库和显式标注的协议样本，验证检索状态、打字显示、来源展开、追问、刷新与停止；没有产生真实模型调用，未修改实际工作库。预览见[聊天界面](chat-preview.png)，图中内容为联调样本而非真实单位制度。实际推理仍需按模型接入指南配置服务与密钥。

## 申请草稿授权

`POST /chat` 可选 `allow_draft: boolean`，默认 false，由用户勾选输入区授权选项后发送。该值从HTTP请求注入工具上下文，模型参数不能覆盖。事项和校验凭证跨轮持久化；无来源时允许追问，不强制检索。草稿接口、工具前置条件和证据限制见 [档案智能体](autonomous-agent.md)。
