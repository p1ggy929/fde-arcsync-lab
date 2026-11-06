# 智能体与数据模型接入

第三、四层代码已实现。默认 `ARCSYNC_LLM_PROVIDER=disabled`，普通业务继续可用；智能查阅明确显示未配置，不返回固定的“AI 答案”。按本次约定，实际模型地址、模型名及密钥由后续配置提供。

## 1. 本地配置

复制项目根目录 `.env.example` 为 `.env`，编辑后重新启动。`.env` 已被 Git 与交付包排除，密钥只在服务端使用。系统环境变量优先于 `.env`，修改配置后须停止原启动窗口再重新双击启动脚本。

### Ollama

先在本机运行 Ollama，并准备一个支持 JSON 输出、工具调用的对话模型，以及一个嵌入模型。可通过 `ollama list` 检查已安装模型。应用不自动下载模型，也不会替你安装 GPU 驱动。

```dotenv
ARCSYNC_LLM_PROVIDER=ollama
ARCSYNC_LLM_BASE_URL=http://127.0.0.1:11434
ARCSYNC_LLM_MODEL=填写已安装的对话模型名
ARCSYNC_LLM_API_KEY=
ARCSYNC_EMBEDDING_PROVIDER=ollama
ARCSYNC_EMBEDDING_BASE_URL=http://127.0.0.1:11434
ARCSYNC_EMBEDDING_MODEL=填写已安装的嵌入模型名
ARCSYNC_EMBEDDING_API_KEY=
```

对话调用 `/api/chat`，嵌入调用 `/api/embed`；流式回答消费 Ollama NDJSON。模型需能在传入工具列表后返回原生 `tool_calls`，模型自主选择检索、核验、收集信息等工具，不再有固定意图分类。

### vLLM

对话服务需启用所选模型对应的工具解析配置；对话与嵌入通常使用不同模型服务，具体启动参数取决于所选模型，按 vLLM 官方说明配置。

```dotenv
ARCSYNC_LLM_PROVIDER=vllm
ARCSYNC_LLM_BASE_URL=http://127.0.0.1:8000/v1
ARCSYNC_LLM_MODEL=填写对话服务实际模型名
ARCSYNC_LLM_API_KEY=如服务要求鉴权则填写
ARCSYNC_EMBEDDING_PROVIDER=vllm
ARCSYNC_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
ARCSYNC_EMBEDDING_MODEL=填写嵌入服务实际模型名
ARCSYNC_EMBEDDING_API_KEY=如服务要求鉴权则填写
```

使用兼容的 `/chat/completions`、`/embeddings`，对话支持 `response_format` JSON 和原生函数工具；流式回答消费 SSE。远程部署请使用 HTTPS 地址。

### 云端 API

提供方需支持同一兼容协议：Chat Completions、原生工具调用、JSON 输出、SSE，以及嵌入接口。对话与嵌入可以来自不同提供方，分别配置地址和密钥；不依赖某个指定模型品牌。

```dotenv
ARCSYNC_LLM_PROVIDER=cloud
ARCSYNC_LLM_BASE_URL=https://你的服务域名/v1
ARCSYNC_LLM_MODEL=填写服务模型名
ARCSYNC_LLM_API_KEY=填写真实密钥
ARCSYNC_EMBEDDING_PROVIDER=cloud
ARCSYNC_EMBEDDING_BASE_URL=https://你的嵌入服务域名/v1
ARCSYNC_EMBEDDING_MODEL=填写嵌入模型名
ARCSYNC_EMBEDDING_API_KEY=填写真实密钥
```

不要把 `/chat/completions` 或 `/api/chat` 追加到配置地址，应用会自动拼接。远程端点只接受 HTTPS；用户名、密码和查询参数不能嵌入端点地址。模型请求设有连接与读取超时，不展示上游响应正文或密钥。

## 2. 检查、导入与查阅

1. 重新启动，以 `engineer` 或 `protector` 登录后台，进入“模型与知识”，点击“检查模型连接”。此操作只验证一次对话和一次嵌入连通性，不代表工具质量或业务准确率已通过验收。
2. 在同页导入资料：档号、标题、新版本、年度、类别、扫描页、纸质页与正文。界面单次导入一页；API 支持一个版本 1–20 页，整个 JSON 请求仍受 32 KiB 限制，单页最多 5000 字。多页同版本应一次提交；同一版本不追加或覆盖正文。
3. 更新已有资料时，提供新版本及 `expected_version`。原版文本保留；扫描页与纸质页当前要求固定、正向页码偏移。缺少页文本返回不可用，不补造原文。
4. 进入“智能查阅”，可提问“比较总体规划批复与后续调整”“查询 CY-2026-1023-001 的进度”或“异常修复后怎么恢复”。
5. 页面展示决策状态、工具状态、来源、生成草稿和最终回答。来源编号、权限或版本校验失败时撤回草稿；回答中的事实和适用性仍须人工复核。需要引用入业务时，打开资料页，使用原有人工确认流程。

预置资料只有合成目录摘要，没有真实 OCR。导入页文本是操作人员提供的文本，仍不能当作已经核验的原始扫描件。真实档案库与 OCR 同步接口没有在本次自动接通。

## 3. FAISS 与缓存

FAISS `IndexFlatIP` 对归一化向量执行余弦检索，再与关键词排名进行倒数排名融合。候选集先按当前账号、开放范围、资料状态和版本筛选，受限资料不先进入全局索引再事后过滤。

嵌入缓存保存在业务库 `vector_embeddings`，以块 ID、模型端点/模型名指纹及内容摘要隔离；对话模型和嵌入模型可独立切换。查询嵌入每次重新计算，已有资料向量可复用。不要在相同端点和模型名下静默替换不兼容的嵌入模型；如维度改变会拒绝混用，应更改模型名或清理对应缓存后重建。

每个授权资料快照另存 FAISS 索引文件于 `data/vectors/`（可用 `ARCSYNC_VECTOR_DIR` 改路径），不经 HTTP 提供、不会打进交付包。重启后从数据库向量缓存重建内存索引，不加载外来二进制或 pickle。当前单次候选上限 2000 块，每批嵌入 16 块；单次智能任务最多保留12条来源，工具消息超过60000字符会停止，最终每条依据上下文最多4000字符；这适用于本地试点，尚未实现分布式索引和大规模增量任务。

默认所有模型上下文只使用公开有效资料。只有保护专员、两个端点均在本机、两个提供方均不是 cloud，并显式配置 `ARCSYNC_ALLOW_RESTRICTED_MODEL_CONTEXT=true`，才允许受限资料参与检索。生成阶段和历史回答读取都会复查权限、内容与版本。

## 4. MySQL 业务库

默认继续使用原 SQLite，保留现有数据。设置 `ARCSYNC_DATABASE_URL` 后切换为 MySQL；不能把一份新 MySQL 当成已经迁移了旧业务。

```dotenv
ARCSYNC_DATABASE_URL=mysql://arcsync:URL编码后的密码@127.0.0.1:3307/arcsync
```

支持 MySQL 8.0.13+，本地部署模板使用 8.4 LTS。账号需建表、建触发器和数据读写权限，数据库预先创建并采用 utf8mb4。已有正式数据库应由运维先审核 schema 和权限，不要向不相关的现有业务库直接初始化。没有 MySQL 连接池或数据库自动备份；每次操作独立连接。

MySQL 适配覆盖原有用户/会话、档案、业务、交接、异常、事件、反馈、配置与审计，以及新增资料块、向量缓存、智能体运行与步骤。参数化 SQL 使用 PyMySQL，写事务沿用 revision 校验；MySQL 以库内命名锁对业务写入串行化，成功或失败都释放。审计表禁止 UPDATE/DELETE 的触发器同样创建。DDL 位于 `data_models/mysql.py`。

可选本地容器：先在 `.env` 填写 `ARCSYNC_MYSQL_ROOT_PASSWORD`、`ARCSYNC_MYSQL_PASSWORD`，再运行：

```sh
sh scripts/start_mysql.sh
```

需要本机已有 Docker Engine/Docker Desktop 正在运行；容器只绑定 127.0.0.1:3307，数据写入命名卷。脚本不会自动安装或启动 Docker Desktop。初始化密码只影响全新数据卷，不能修改已有 MySQL 账号密码。

迁移现有 SQLite：先停止平台、备份源文件，并把 `.env` 中 MySQL URL 指向专用空库，再执行：

```sh
.venv/bin/python scripts/migrate_to_mysql.py --source data/arcsync.sqlite3
```

目标只允许空库；拒绝覆盖已有数据。复制账号、业务、知识、向量缓存及智能体记录，不复制会话，迁移后重新登录。源 SQLite 以只读方式打开并保留；建表是 MySQL DDL，失败时可能留下空表，数据复制事务会回滚。不同端点下的向量缓存会因指纹不同而重新生成。

## 5. 验证边界

自动测试通过 HTTP 返回样本模拟模型协议，并使用真实 FAISS 执行检索，覆盖编排、工具、引用、权限、版本变化与持久化。样本响应仅存在于 tests，不作为产品运行模式。

本机当前没有已配置的模型服务/密钥，也没有运行中的 MySQL。尚未执行真实模型推理、GPU 负载测试、真实 MySQL 数据迁移或 MySQL 并发压测；完成配置后按 [智能体验收流程](agent-test-guide.md) 逐项执行。

协议依据：[Ollama Chat](https://docs.ollama.com/api/chat)、[Ollama Embeddings](https://docs.ollama.com/api/embed)、[vLLM 兼容服务](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)、[FAISS 余弦距离](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances)、[PyMySQL 参数化操作](https://pymysql.readthedocs.io/en/latest/user/examples.html)。
