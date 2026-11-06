# 第一阶段：生产安全与成本控制

2026-10-30。本阶段提供可部署代码和演练工具；未替用户部署公网服务。原有一键启动默认仍为开发模式，不应直接映射公网。

## 实现范围

- `ARCSYNC_ENV=production`：启动前检查随机JWT密钥、Redis、域名、模型价格及预算参数。独立生产空库不生成演示账号、合成档案或样例业务。启用的演示账号、已知演示口令、演示业务、缺少具名管理员均阻止服务启动。设置新环境变量不会自动重置旧库密码。
- 欢迎页生产脚本不提供体验账号与默认口令；生产卷页接口不生成合成正文。生产要求HTTPS和允许的域名；仅信任明确的网关与容器回环代理来源，不信任任意X-Forwarded头。
- 会话、事项和运行历史沿用独立user_id；业务列表、事件、交接、反馈、异常及导航计数使用同一业务可见性过滤，所有业务变更及间接引用先检查对象权限。
- 当前授权模型：查询代表看本人申请；整理员看本人申请及整理岗位当前队列/待接手队列；审核、工程、保护、主任承担机构级处理权限。岗位队列是明确共享范围，不是每位工作人员的独占任务。未实现部门/租户隔离，部署多个机构应分开部署，不能宣称已支持多租户。
- 生产Redis连接须携带认证密码；Redis部署在受控私网，跨可信边界时使用rediss TLS。生产Redis统一HTTP、登录、付费入口请求频率与并发租约；聊天、旧智能体、模型诊断、管理上传均覆盖。Redis不可用时拒绝受控请求，不能静默退回进程内限流。
- 请求并发租约600秒、请求总时限540秒，退出释放，进程崩溃后自动过期；聊天本身仍有180秒总时限。不同入口按user_id共享限制。当前旧的本地保护仍保留，可能更严格，不放宽Redis限制。
- 所有ModelClient的chat/stream/embed在网络调用前原子预留平台及用户的日Token和费用额度，UTC日切；诊断、检索嵌入和文件向量化也计量。未知调用人一律拒绝。
- 80%预算阈值写一次结构化日志告警；管理工作台“模型用量”与`GET /api/admin/usage`可查看预算占用及最多100条调用记录。外部告警通知渠道需要运维接入，不会自动发送消息。

## 预算口径及边界

Token预留基于请求UTF-8字节数、额外封装余量和输出上限，是保守资源估算，不是精确分词。工具模式每次输出上限1600；流式使用实际配置上限。嵌入只预留输入。单次预留默认上限200000 Token，可用ARCSYNC_MAX_CALL_RESERVED_TOKENS调整。

费用单位为micro USD（百万分之一美元）。每百万Token价格乘Token数量，得到micro USD。输入、输出、嵌入价格必须与实际模型及合同对应；本地免费模型可明确设0，但仍受Token预算限制。变更模型或供应商后应先更新价格，不能用旧价保证真实费用。供应商账号也应配置硬预算；字符估算、未知计费项及供应商行为不构成实际账单的绝对保证。

有用量返回时保存供应商报告的输入/输出Token，同时记录模型、调用类型、耗时和状态；未返回的用量保持未知，不填充伪造数值。预留值与实测值分别展示。失败、取消、断连不退预留，以防反复失败绕过配额，可能提前耗尽预算。上传的多批嵌入逐批预留，后批失败时不会发布半成品，但已经发生的前批调用仍计入预算。

Redis使用单实例/主端点Lua原子操作，不支持Redis Cluster分片脚本。预算与台账要求持久化、noeviction和受控管理员权限；不得随意FLUSHDB或只恢复旧Redis快照，否则额度计数可能回退。台账按ARCSYNC_USAGE_RETENTION_DAYS到期，默认90天，正式期限须按组织策略确认。此项只管理模型台账，不代表所有业务/审计日志都已实现自动留存策略。

## 首次部署

1. 使用独立生产环境。将`config/production/env.example`复制为项目根目录`.env.production`，填写域名、密钥、Redis密码及模型价格；模型接入参数沿用`.env.example`。JWT密钥可用`python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`本地生成；不要发到聊天或提交仓库。Redis密码也使用URL安全随机值并保持URL一致。
2. 在项目根目录构建明确版本镜像：`docker build -f config/production/Dockerfile -t arcsync:phase1 .`。模板基础镜像按次版本引用，正式发布须由运维扫描并记录镜像摘要，固定获批摘要后发布。
3. 启动依赖：`docker compose --env-file .env.production -f config/production/compose.yml up -d redis`。Redis无宿主机端口；应用也不直接暴露宿主机端口。
4. 引导具名管理员：`docker compose --env-file .env.production -f config/production/compose.yml run --rm app python scripts/create_user.py --username real-admin --name 管理员姓名 --role director`，密码交互输入。此脚本仅初始化表和账号，不启动未满足安全条件的HTTP服务。为不同员工分别创建账号。
5. 首次启动：`docker compose --env-file .env.production -f config/production/compose.yml up -d`。DNS应指向服务器；仅开放80/443供Caddy签发证书与HTTPS服务。模板固定私网172.30.0.0/24，如与现有网络冲突，必须同时更改网段、网关固定IP和Dockerfile可信代理IP。
6. `/api/health`检查存活及代码构建指纹；`/api/ready`只检查业务库、聊天库与Redis，不调用付费模型。生产探测需HTTPS正确域名；容器内部健康检查走仅信任回环来源的代理头。
7. 完成HTTPS、跨员工授权、真实Redis多进程配额、额度耗尽、模型失败、备份恢复和实际模型用例验收，再开放用户。

MySQL模式另外配置ARCSYNC_DATABASE_URL、ARCSYNC_MYSQL_CA并只读挂载CA文件；客户端强制证书链和主机名验证，数据库必须在私网。模板默认SQLite，适合单宿主机受控上线；SQLite文件不能通过普通网络共享盘扩展成多主机写入。扩大规模前应统一迁移业务与聊天存储。当前初始化还会执行建表，部署时须明确初始化账号/运行账号权限并验证，不能把最小权限仅当模板声明。

## 从演示数据升级

优先使用独立生产空库。若必须迁移旧数据，先停服备份并在副本中清理合成业务及资料、核对人员归属，再迁移。`scripts/disable_demo_accounts.py --confirm`仅显式停用六个演示用户名并撤销其会话，不删除业务、审计或合成数据，不代替完整迁移。旧版JWT密钥轮换后会话需重新登录。不要对真实工作库执行未经验证的清库操作。

## 备份、恢复及回滚

- 先停止所有应用实例写入，保留网关维护提示；正在生成的SSE会中断，员工确认完成的写入以数据库状态为准。旧确认请求不可人工重放。
- SQLite备份：`python scripts/production_backup.py backup --writes-stopped --business /path/business.sqlite3 --chat /path/chat.sqlite3 --destination /secure/new-snapshot`。脚本使用SQLite backup API并校验完整性，不直接复制活动主文件。目的目录必须不存在，输出权限受限。
- 恢复演练：`python scripts/production_backup.py restore --writes-stopped --source /secure/new-snapshot --destination /secure/new-restore`。先核对摘要和数据库完整性，再恢复到新目录，绝不覆盖现库。容器卷路径可在停服后挂载到独立维护容器操作，不要误用宿主机示例路径。
- MySQL需使用组织批准的原生一致性备份/恢复工具；SQLite脚本不支持MySQL。聊天SQLite必须纳入同一停写窗口。另行备份Redis持久化文件、镜像摘要、配置、证书和加密密钥材料；密钥应加密保管，不与明文数据库公开存放。FAISS索引可从授权原文重建。当前导入文本保存在数据库，不含尚未接入的外部原卷文件备份。
- 发布或回切已验证兼容的镜像：`scripts/production_release.sh arcsync:已批准版本`。镜像须事先构建/拉取，脚本部署应用并等待就绪；回滚使用同一脚本传入上一兼容版本。不会自动降级数据库或恢复旧备份，不自动宣称全功能验收通过。
- 本阶段表结构为增量创建，权限与配置行为也有变化。禁止默认回退到没有生产模式保护的旧镜像；上一版本必须具有等价安全边界且通过新库兼容性验证，否则采用前滚修复。若需恢复旧库，应先评估并保全发布后的新增业务，不能静默丢数据。
- 具名发布负责人记录发布包/镜像摘要、配置版本、备份位置、开始结束时间及验证证据。上线前批准RTO/RPO、错误率和费用阈值；出现越权或泄露立即暂停外部流量并按事故预案处理，泄露密钥单独轮换。

## 本地验证与待部署验证

安装`requirements-dev.txt`后运行`scripts/test.sh`。新增测试执行真实Lua语义的fakeredis契约验证，覆盖原子预留竞争、共享频率、租约过期、预算拒绝、Redis失败、流式完成/取消、生产配置和账号拒绝、资源脚本语法、跨员工业务隔离、备份完整性及恢复不覆盖。

当前机器Docker CLI可用但daemon未运行，尚未启动真实Redis、构建/运行生产容器或取得真实域名证书；也未调用真实付费模型、连接生产MySQL。Compose配置可静态验证。发布方仍须在目标机器完成真实Redis故障、多进程压测、HTTPS、MySQL TLS、预算和端到端恢复演练。第一阶段代码交付不等于批准公网开放。
