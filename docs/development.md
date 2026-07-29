# 开发约定

## 环境准备

需要 Python 3.12+、uv、Node.js 22+、pnpm 和 Docker。首次启动：

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
uv sync --project apps/api --all-groups
pnpm install
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
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
uv run --project apps/api alembic -c apps/api/alembic.ini revision --autogenerate -m "add documents"
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
uv run --project apps/api alembic -c apps/api/alembic.ini downgrade -1
```

## 本地嵌入模型

默认模型为固定 revision 的 `BAAI/bge-m3`，只使用 1024 维 dense embedding。Worker 首次
执行 embedding 时才加载模型，模型缓存在宿主机 `D:\DevelopEnvironment\huggingface`。
仓库、上传目录和 Docker 镜像均不保存模型权重。

默认配置优先使用 `https://hf-mirror.com`。网络环境允许直连 Hugging Face 时，将
`EMBEDDING_HF_ENDPOINT` 设为空；也可以先通过 ModelScope 下载完整模型目录，再把
`EMBEDDING_MODEL` 设置为该本地目录。两种方式都应继续把文件放在统一的宿主机缓存根目录，
不要提交模型文件。

`EMBEDDING_DEVICE=cpu` 是跨机器默认值。安装匹配的 CUDA 版 PyTorch 后可改为 `cuda`，
业务代码和数据库契约无需变化。`EMBEDDING_BATCH_SIZE` 应根据实际内存或显存 smoke test
调整，不能把未经测量的吞吐量写入文档。

项目使用 `sentence-transformers`，因为 BGE-M3 官方发布了对应的 pooling 与归一化模型图；
直接使用底层 Transformers 需要自行重复这些模型特定推理规则，更容易产生与查询侧不一致的
向量。适配器仍显式请求归一化并校验维度与范数，避免第三方模型输出静默污染 pgvector。

## 回答模型供应商

回答生成通过 OpenAI 兼容的远程 Chat Completions API 接入。配置 `LLM_BASE_URL`、
`LLM_API_KEY` 和 `LLM_MODEL` 后，API 进程直接请求供应商；回答模型权重不会下载到本机，
也不会进入仓库或 Docker 镜像。本项目默认模型名仅是远程供应商路由标识。

追问查询改写使用同一供应商，并由 `LLM_QUESTION_REWRITE_PROMPT_VERSION` 记录提示词版本。
`ANSWER_CONTEXT_QUESTION_LIMIT` 限制可用于指代消解的近期用户问题数量；历史模型回答不会
发送给改写器，也不会成为后续回答证据。

证据判断和引用修复同样使用 OpenAI 兼容供应商，但通过独立端口和严格 JSON 契约接入。
`LLM_EVIDENCE_ASSESSMENT_PROMPT_VERSION` 与 `LLM_CITATION_REPAIR_PROMPT_VERSION` 分别记录
两个决策提示词版本。每个 Answer Run 会同时保存这些版本以及
`ANSWER_WORKFLOW_VERSION=langgraph-bounded-v1`，用于重放时识别完整决策配置。

可使用以下命令做最小连通性检查：

```powershell
uv run --project apps/api python apps/api/scripts/probe_llm_provider.py
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

## API 与错误

所有业务接口放在 `/api/v1` 下。错误响应必须使用统一结构；业务代码抛出 `AppError`
子类，由全局处理器映射为 HTTP 响应。不要在 Service 中抛 `HTTPException`。

## 配置与秘密

后端配置集中在 `core/config.py`，前端只读取 `VITE_` 前缀变量。新增配置时同步更新
`.env.example`，示例值不能是真实密钥。生产环境由部署平台注入配置，不复制开发 `.env`。

## 架构决策记录

对数据库替换、认证方式、队列实现、模型供应商抽象等难以逆转的决定，在 `docs/adr/`
新增编号文档，记录背景、决定、替代方案与后果。小型可逆实现不需要 ADR。
