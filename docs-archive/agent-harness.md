# 云州档案 Agent Harness：5轮循环、决策日志与人工确认

## 框架选择

采用项目内轻量 `AgentHarness`（Python 异步生成器），复用现有 FastAPI、SQLite/MySQL、模型适配器和严格工具协议。Harness负责执行预算、日志和暂停点；Orchestrator负责登录状态、来源复核和聊天事件。没有新增外部框架依赖。

选择依据：项目已有可用的模型接口、工具及服务端权限；显式工具循环便于审计和控制。参照[Anthropic 官方的有效 Agent 实践](https://www.anthropic.com/research/building-effective-agents)关于从简单可组合结构开始、由工具反馈驱动决策并保留人工监督的建议。

## 执行循环与预算

`模型决策 → 参数/权限检查 → 工具执行 → 工具结果回填 → 再决策`，单次运行硬上限5轮。即使配置max_rounds大于5，Harness仍限制为5。可将互不依赖的只读工具放在同一轮；校验和创建不得同轮执行。

每一轮只调用一次模型。模型不再调用工具时，其content就是最终回答，服务端校验引用并分块通过SSE展示；不再额外调用第6次模型来润色最终答案。最终文字是在模型决策返回后分块发送，不声称是上游模型逐token输出。草稿预览由服务端校验后的数据生成，逐块通过SSE发送。

第5轮若仍请求工具，执行预算内工具后结束，明确说明轮次上限与未完成事项；记录limit_reached。补充信息后的新员工消息是新运行，重新计数，不能把未完成操作当作成功。确认恢复只执行已冻结的创建动作，不再启动模型或重置轮次。

## 创建前强制中断

1. 模型调用validate_access_request，服务器校验通过并签发当前版本凭证。
2. 下一轮模型请求create_access_draft。Harness只创建agent_confirmations待确认记录，**access_drafts此时没有新增**。
3. SSE发送多个preview_delta，最后发送confirmation_required。内容包含事项、申请单位、目的、利用方式、档案标题/编号/版本、扫描页及纸质页。此时结束流并释放生成任务，不持有连接等人。
4. 员工点击“确认内容并保存草稿”，客户端POST明确决定与preview_hash。
5. 服务端核验登录人员、预览hash、事项版本、档案内容/开放状态、页码和有效期，在同一事务中创建草稿、消耗待确认记录并记执行日志。
6. 员工取消则不创建。确认重复提交返回同一个结果；多线程重复确认也只保存一份。草稿之后仍须员工到申请草稿页人工提交权限核验。

预先授权参数allow_draft仅为旧客户端保留的兼容字段，不再允许绕过具体内容确认。模型没有确认工具，不能传approved=true跳过暂停。确认绑定预览，改动内容须重新校验和重新展示。

待确认记录持久化，刷新或重启后由GET恢复；账号、权限或资料变化后停止确认并提示重新核验。凭证及确认最长15分钟有效。跨库聊天消息只是业务确认结果的镜像，业务事务结果为准，GET可幂等修复镜像。暂停状态不进入下一轮模型历史。

## 日志与阶段5分析

`agent_harness_runs`：id、user_id、状态、round_count、max_rounds、关联会话/消息、创建和更新时间。

`agent_decision_logs`：id、run_id、user_id、round_no、kind、call_id、tool_name、arguments_json、duration_ms、status、result_summary、created。

- kind=decision：每轮一条，tool_name=__decision__，记录模型决策耗时和所选工具名/直接回答，不存隐藏思考或完整回答。
- kind=tool：每个实际尝试的工具一条，包含参数、执行耗时和最多800字的结果摘要。失败、参数/权限拒绝和等待确认均记录。
- kind=resume：员工确认/取消后记录实际创建或拒绝，沿用原run_id与round_no，不新增模型轮次。
- 创建提议日志waiting_confirmation表示尚未执行创建。实际创建成功看resume/completed。原agent_tool_audit仍保留完整工具结果。
- 日志参数包含业务标识和校验凭证，仅管理员可读；阶段5导出应按所需字段选取，结果摘要不含完整档案正文和模型思考。

管理员可在 `/admin/manage?section=audit` 按员工及时间查看日志。API：

`GET /api/admin/agent-decisions?employee=用户ID&run_id=运行ID&tool=工具名&start=含时区时间&end=含时区时间&offset=0`

每页50条，返回items、has_more、next_offset。

阶段5示例（SQLite/MySQL均支持）：

```sql
-- 每个工具的调用量及平均耗时；等待确认与恢复执行分开统计。
SELECT tool_name, kind, status, COUNT(*) AS calls,
       AVG(duration_ms) AS avg_duration_ms
FROM agent_decision_logs
GROUP BY tool_name, kind, status;

-- 每次运行用了多少轮，有多少触顶、暂停或完成。
SELECT status, round_count, COUNT(*) AS runs
FROM agent_harness_runs
GROUP BY status, round_count;
```

## SSE与确认接口

正常：session → decision/status → delta → citations → done。

中断：session → decision/status → preview_delta(多次) → confirmation_required → 结束。该分支没有done，也没有创建草稿。

confirmation_required包含id、run_id、round_no、status、preview、preview_hash、expires_at和关联session_id/message_id。preview是已校验的公开预览，不含完整档案正文或校验快照。

- `GET /api/agent/confirmations?session_id=...`：本人待确认和最近结果；过期/资料变化返回stale且不返回预览。
- `POST /api/agent/confirmations/{id}/resolve`：`{"approved":true,"preview_hash":"..."}`，需JSON请求头和X-ArcSync-Request: 1。
- 返回completed及draft，或cancelled。无权404，内容不匹配/过期/资料变化409。取消后不能再改为确认。

## 三个典型档案对话的预期轨迹

以下为预期轨迹，自动测试用协议替身验证执行约束，不代表已用真实生产模型验证成功率。

### A．“公开档案查阅需要哪些手续？”

| 轮次 | 模型决策/工具 | 结果与后续 |
|---|---|---|
| 1 | get_access_rules；需要具体制度时search_archives | 取得当前流程及授权制度来源 |
| 2 | 不调用工具，给出引用答案 | 说明申请、权限核验、人工引用确认和审核交付 |

不调用update_access_case、validate_access_request、create_access_draft，也不创建任何申请或草稿。

### B．“帮我申请查阅1986年县城总体规划批复”

第一次消息先检索候选档案，并追问申请单位、查阅目的与利用方式，不能编造这些信息。该次运行结束。

员工补充：“云州规划研究室，研究规划沿革，现场查阅批复第6页；请创建草稿。”进入新运行：

| 轮次 | 模型决策/工具 | 结果与后续 |
|---|---|---|
| 1 | read_archive_page、check_archive_validity、update_access_case | 读取当前页，核对当前版本并保存完整需求；必要时读取已知后续更正正文 |
| 2 | 更正未读则先读取；否则validate_access_request | 未满足条件时明确返回needs_input/needs_review；通过才有validation_id |
| 3 | 如上一轮是补读，此轮校验；否则请求create_access_draft | 创建请求触发暂停，流式展示具体预览，此时无草稿 |
| 4（需要时） | 校验通过后请求create_access_draft | 必须与校验分轮；流式预览后暂停 |
| 人工恢复 | 员工确认 → 执行create_access_draft | 复核冻结版本与资料，保存草稿；不再调用模型 |

可用信息不足或更正链过长时在5轮内停止并说明下一步，不能绕过。只有目录摘要、扫描正文未导入或页码歧义时不会通过校验。

### C．“这份档案扫描第4页能作为当前查阅依据吗？”

若没有明确档案编号，先追问编号；有编号后：

| 轮次 | 模型决策/工具 | 结果与后续 |
|---|---|---|
| 1 | check_archive_validity + read_archive_page | 检查版本、质量、更正关系和页文本 |
| 2 | 必要时读取更正正文；否则直接回答 | 失效或缺页说明原因，有效且可读也说明仍须人工卷页确认 |
| 3（需要时） | 不调用工具，给出引用结论 | 不创建事项、校验申请或生成草稿 |

## 运行与验证

重启服务即可增量建立日志、待确认和运行归属表。旧的无独立人员归属运行不会通过员工运行历史接口返回；新运行按user_id隔离。离线MySQL迁移包含新增表，不复制登录会话。

自动测试覆盖5次模型调用硬上限、直接回答记录、耗时日志、工具失败、预览流、创建前零草稿、恢复幂等、取消、跨员工、内容hash、过期、资料变更、会话刷新与历史隔离。真实推理质量仍需配置实际模型及可用档案正文后验证。
