# 开发约定

## 环境准备

SourceTrace 支持完整 Compose 与混合开发两种方式。两者使用相同业务代码和数据库迁移，
不要同时启动它们占用相同的 `5173`、`8000` 端口。

### 完整 Docker Compose

完整模式只要求 Docker Desktop。创建 `.env` 并填写远程回答模型配置后启动：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

基础 Compose 使用 CPU 配置，包含 Web、API、Worker、PostgreSQL、Redis 和一次性迁移容器。
API 只有在迁移成功且数据库、Redis 均就绪后才对外提供服务。可使用以下命令观察状态：

```powershell
docker compose logs -f api worker
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://localhost:8000/ready
Invoke-WebRequest http://localhost:5173/web-health
```

使用 NVIDIA GPU 运行 Worker 时，需要宿主机已安装可供 Docker 使用的 NVIDIA 驱动和
Container Toolkit，然后叠加 GPU override：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
```

GPU override 只把 Worker 的 `EMBEDDING_DEVICE` 改为 `cuda` 并请求 GPU；API 和迁移仍使用
基础配置。停止服务使用 `docker compose down`。该命令保留数据库和上传卷；只有确认要删除
所有本地数据时才使用 `docker compose down -v`。

### 混合开发

混合模式需要 Python 3.12+、uv、Node.js 22+、pnpm 和 Docker。PostgreSQL 与 Redis 在
Docker 中运行，API、Worker 和 Web 在宿主机运行，适合热更新与调试：

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
uv sync --project apps/api --all-groups --extra cpu
pnpm install
uv run --project apps/api --extra cpu alembic -c apps/api/alembic.ini upgrade head
pnpm dev
```

`pnpm dev` 会同时运行 API、Dramatiq 摄取 Worker 和 Web。只调试后台摄取时可运行
`pnpm dev:worker`；Worker 与 API 共享应用代码，但作为独立进程消费 Redis 任务。

摄取使用 Dramatiq 是因为项目需要 Redis 队列的确认、有限重试与退避语义，标准库不提供
跨进程可靠队列。文本切分使用 tiktoken 是因为片段窗口必须按模型 token 计数并可重放，
字符或空白切分无法提供等价边界。

## 日常流程

1. 从一个可验证的用户行为定义改动，不按技术层批量铺空代码。
2. 在对应 feature 内完成 Router、Service、Repository 的最小纵向切片。
3. 先写纯业务单元测试，再按风险补充 API/数据库集成测试。
4. API schema 变化后运行 `pnpm generate:api` 更新前端类型。
5. 提交前运行 `pnpm check`、`pnpm test` 和 `pnpm build`。

## 新增后端模块

例如新增 `documents`：

```text
modules/documents/
  __init__.py
  models.py
  schemas.py
  repository.py
  service.py
  router.py
  dependencies.py
```

不需要数据库时不要创建 `models.py` 和 `repository.py`；依赖装配简单时也不创建
`dependencies.py`。Router 在 `api/router.py` 注册。模块测试对应放在 `tests/unit/` 和
`tests/integration/`，不把测试塞进源码目录。

## 数据库迁移

```powershell
uv run --project apps/api --extra cpu alembic -c apps/api/alembic.ini revision --autogenerate -m "add documents"
uv run --project apps/api --extra cpu alembic -c apps/api/alembic.ini upgrade head
uv run --project apps/api --extra cpu alembic -c apps/api/alembic.ini downgrade -1
```

## 本地嵌入模型

默认模型为固定 revision 的 `BAAI/bge-m3`，只使用 1024 维 dense embedding。Worker 首次
执行 embedding 时才加载模型。混合开发由 `EMBEDDING_CACHE_DIR` 指定宿主机缓存；完整
Compose 默认使用仓库下被忽略的 `./data/huggingface`，并挂载到容器固定路径
`/models/huggingface`。例如本机已有 `D:\DevelopEnvironment\huggingface` 缓存时，在 `.env`
中设置：

```dotenv
HF_CACHE_HOST_PATH=D:\DevelopEnvironment\huggingface
```

这样容器直接复用已有文件，不会把权重复制到镜像。仓库、上传目录和 Docker 镜像均不保存
模型权重。

`EMBEDDING_MODEL` 供宿主机进程使用，可能是 Windows 本地路径；完整 Compose 使用独立的
`EMBEDDING_MODEL_CONTAINER`，避免把宿主机路径原样传入 Linux 容器。默认值
`BAAI/bge-m3` 会在挂载缓存中查找或下载模型。若缓存根目录中已有 ModelScope 完整模型，
可同时配置：

```dotenv
HF_CACHE_HOST_PATH=D:\DevelopEnvironment\huggingface
EMBEDDING_MODEL_CONTAINER=/models/huggingface/modelscope/BAAI/bge-m3
```

默认配置优先使用 `https://hf-mirror.com`。网络环境允许直连 Hugging Face 时，将
`EMBEDDING_HF_ENDPOINT` 设为空；也可以先通过 ModelScope 下载完整模型目录，再把
`EMBEDDING_MODEL` 设置为该本地目录。两种方式都应继续把文件放在统一的宿主机缓存根目录，
不要提交模型文件。

`EMBEDDING_DEVICE=cpu` 是跨机器默认值。混合开发安装匹配的 CUDA 版 PyTorch 后可改为
`cuda`；完整 Compose 使用 `compose.gpu.yaml`，业务代码和数据库契约均无需变化。
`EMBEDDING_BATCH_SIZE` 应根据实际内存或显存 smoke test 调整，不能把未经测量的吞吐量写入
文档。

基础 Compose 使用 `cpu` optional dependency，并从 PyTorch 官方 CPU 索引安装不含 CUDA
运行库的 wheel。GPU overlay 只为 Worker 构建独立的 `sourcetrace-worker:gpu` 镜像，使用
`cu130` optional dependency；API 和迁移容器继续使用 CPU 镜像。两个 extra 互斥，禁止在同一
环境中同时安装。`python apps/api/scripts/verify_cpu_dependencies.py` 会在同步依赖前检查 CPU
导出结果，防止基础镜像再次引入 NVIDIA 或 Triton 运行库。

项目使用 `sentence-transformers`，因为 BGE-M3 官方发布了对应的 pooling 与归一化模型图；
直接使用底层 Transformers 需要自行重复这些模型特定推理规则，更容易产生与查询侧不一致的
向量。适配器仍显式请求归一化并校验维度与范数，避免第三方模型输出静默污染 pgvector。

## 回答模型供应商

回答生成通过 OpenAI 兼容的远程 Chat Completions API 接入。配置 `LLM_BASE_URL`、
`LLM_API_KEY` 和 `LLM_MODEL` 后，API 进程直接请求供应商；回答模型权重不会下载到本机，
也不会进入仓库或 Docker 镜像。本项目默认模型名仅是远程供应商路由标识。

`LLM_TIMEOUT_SECONDS` 约束每次供应商请求的完整生命周期，包括流式回答以及问题改写、证据判断
和引用修复的非流式结构化调用。供应商发送的 SSE 或空行 keep-alive 注释可以保持底层连接，但
不能无限延长回答运行；超时统一映射为 `LLM_TIMEOUT`。

`LLM_STRUCTURED_OUTPUT_MODE=json_object` 只在供应商明确兼容 OpenAI JSON Output 时启用。它会
为问题改写、证据判断和引用修复发送 `response_format: {"type": "json_object"}`；若供应商成功响应
但返回空正文，客户端在同一总时限内仅重试一次，随后报告 `LLM_INVALID_RESPONSE`。

`LLM_STRUCTURED_OUTPUT_THINKING` 默认为 `default`，不向供应商发送 thinking 控制参数。仅当供应商
明确支持该 OpenAI 兼容扩展时，可以设为 `enabled` 或 `disabled`；它只影响格式化的内部决策调用，
不改变面向用户的流式回答生成。

追问查询改写使用同一供应商，并由 `LLM_QUESTION_REWRITE_PROMPT_VERSION` 记录提示词版本。
`ANSWER_CONTEXT_QUESTION_LIMIT` 限制可用于指代消解的近期用户问题数量；历史模型回答不会
发送给改写器，也不会成为后续回答证据。

证据判断和引用修复同样使用 OpenAI 兼容供应商，但通过独立端口和严格 JSON 契约接入。
`LLM_EVIDENCE_ASSESSMENT_PROMPT_VERSION` 与 `LLM_CITATION_REPAIR_PROMPT_VERSION` 分别记录
两个决策提示词版本。每个 Answer Run 会同时保存这些版本以及
`ANSWER_WORKFLOW_VERSION=langgraph-bounded-v1`，用于重放时识别完整决策配置。

可使用以下命令做最小连通性检查：

```powershell
uv run --project apps/api --extra cpu python apps/api/scripts/probe_llm_provider.py
```

探针只输出模型名、流式分片数和字符数，不输出密钥、提示词或回答正文。单元、集成和契约
测试仍必须使用 fake 或 HTTP mock，不依赖真实供应商，也不产生模型费用。

自动生成后必须审阅 SQL。外键明确 `ON DELETE` 行为，查询、连接和排序字段按实际访问
模式建索引。不要在应用启动时隐式建表。

## 测试策略

- 单元测试不连接数据库、Redis 或真实模型服务。
- 集成测试使用隔离测试库，外部模型通过 HTTP fake 或确定性 adapter 替换。
- 契约测试校验 OpenAPI 能生成前端类型，并覆盖每个供应商适配器的超时与错误映射。
- 端到端测试只覆盖上传、摄取、提问、引用跳转等关键旅程。
- RAG 效果通过 `evals/` 中版本化样本评测，不把主观示例当准确率。

## RAG 评测

评测数据、fixture 和 JSON Schema 位于 `evals/`。日常开发运行 `pnpm eval:fake`，它只消费
仓库内的确定性 JSON，不连接 PostgreSQL，也不加载本地 embedding 模型或调用 LLM。修改
Pydantic 评测契约后运行 `pnpm generate:eval` 并提交同步生成的 schema。

真实评测通过 `python -m sourcetrace.evaluation.cli real` 单独运行，必须传入 reviewed 数据集、
当前 Git commit、输出路径和 `--confirm-real-provider`。该命令复用生产 `RetrievalService`、
BGE-M3、pgvector 和 `AnswerWorkflow`，但检索只允许数据集声明的不可变文档版本快照，并从
对应 completed ingestion run 读取实际 parser、切分和 embedding provenance。每个 case 使用
独立 run 且不携带会话历史。报告同时记录四个 prompt 版本、检索数量和门禁阈值。回答的人工
端到端结论必须在运行后通过 `review` 命令应用，judgment 文件用 SHA-256 绑定原始待审报告，
禁止把旧结论套到新一轮模型输出。
基础设施
错误会终止评测，不能被统计为回答失败。完整参数和数据审核规则见 `evals/README.md`。

## API 与错误

所有业务接口放在 `/api/v1` 下。错误响应必须使用统一结构；业务代码抛出 `AppError`
子类，由全局处理器映射为 HTTP 响应。不要在 Service 中抛 `HTTPException`。

## 配置与秘密

后端配置集中在 `core/config.py`，前端只读取 `VITE_` 前缀变量。新增配置时同步更新
`.env.example`，示例值不能是真实密钥。生产环境由部署平台注入配置，不复制开发 `.env`。

## 架构决策记录

对数据库替换、认证方式、队列实现、模型供应商抽象等难以逆转的决定，在 `docs/adr/`
新增编号文档，记录背景、决定、替代方案与后果。小型可逆实现不需要 ADR。
