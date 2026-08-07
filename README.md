# SourceTrace

SourceTrace 是一个强调证据可定位、回答可追溯和证据不足时拒答的 RAG 知识库应用。

SourceTrace is a strictly grounded Agentic RAG application: every completed answer must cite
inspectable PDF evidence, while insufficient evidence produces an explicit refusal.

当前仓库采用模块化单体架构：FastAPI API 与异步 Worker 共享 Python 领域代码，Vue 3 前端通过 OpenAPI 契约访问后端，PostgreSQL/pgvector 保存业务数据与向量，Redis 承担任务队列和短期缓存。

## 快速开始

### 完整 Docker Compose

只需 Docker Desktop 即可启动 Web、API、Worker、PostgreSQL 和 Redis。先从示例生成本地
配置，并填写 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Web 默认运行在 `http://localhost:5173`，API 默认运行在 `http://localhost:8000`。Compose
会先等待 PostgreSQL 就绪并执行 Alembic 迁移，迁移成功后才启动 API 和 Worker。停止服务时
运行 `docker compose down`；除非明确要清空数据，不要添加 `-v`。

### 混合开发

前置条件：Python 3.12+、uv、Node.js 22+、pnpm 10+ 和 Docker。

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
uv sync --project apps/api --all-groups --extra cpu
pnpm install
pnpm dev
```

API 默认运行在 `http://localhost:8000`，Web 默认运行在 `http://localhost:5173`。健康检查为 `GET /health`，依赖就绪检查为 `GET /ready`，OpenAPI 文档为 `/docs`。

首次处理文档时，Worker 会通过配置的 Hugging Face 镜像下载 BGE-M3；回答检索使用固定
revision 的 `BAAI/bge-reranker-v2-m3` 对 RRF 候选池重排。完整 Compose 默认将
宿主机 `./data/huggingface` 挂载到容器 `/models/huggingface`；已有缓存可通过
`HF_CACHE_HOST_PATH` 复用。模型缓存不进入 Git 或 Docker 镜像；CPU、NVIDIA GPU、镜像切换
和本地模型目录配置见 [开发约定](docs/development.md)。

## 常用命令

```powershell
pnpm dev          # 同时启动 API、摄取 Worker 与 Web
pnpm dev:api      # 仅启动 API
pnpm dev:worker   # 仅启动 Dramatiq 摄取 Worker
pnpm dev:web      # 仅启动 Web
pnpm check        # 后端静态检查与前端类型检查
pnpm test         # 运行后端和前端测试
pnpm build        # 构建前端
pnpm generate:api # 从 FastAPI 重新生成前端 OpenAPI 类型
pnpm generate:eval # 重新生成评测 JSON Schema
pnpm eval:fake    # 离线重放四类确定性评测 fixture
pnpm eval:rerank  # 对既有真实报告的固定候选池运行本地 reranker 实验
pnpm eval:review  # 将绑定报告摘要的人工 judgment 应用到既有报告
```

## 完整 MVP 验收

完整 Compose 启动后，可通过公开 HTTP/SSE 接口运行一次可清理的真实旅程。该命令会调用已
配置的 embedding 和回答模型，覆盖上传、摄取、引用回答、拒答、取消与历史记录，但不会
输出密钥或回答正文：

```powershell
uv run --project apps/api --extra cpu python apps/api/scripts/verify_mvp.py `
  --output output/verification/mvp.json
```

2026-07-29 的实际验收结果、环境版本、质量门禁和评测限制见
[MVP 验收记录](docs/verification.md)。项目代码调用链、技术原理和常见面试追问见
[代码与面试 Walkthrough](docs/walkthrough.md)，开发中真实遇到的问题见
[问题日志](docs/problem-log.md)。

## 目录

```text
apps/
  api/            FastAPI、领域模块、RAG 工作流与 Worker 入口
  web/            Vue 3 前端，按业务 feature 组织
docs/             架构决策、开发规范和 API 约定
evals/            版本化评测集与评测配置
infra/            数据库初始化、反向代理和部署配置
scripts/          开发、契约生成和质量检查脚本
data/uploads/     本地开发上传目录，不提交用户文件
```

产品范围与验收标准见 [规格说明](docs/specification.md)，工程边界见
[架构说明](docs/architecture.md)，本地流程见 [开发约定](docs/development.md)。

## 当前阶段

仓库已具备知识库、不可变文档版本、异步 PDF 解析切分、本地 dense embedding、
知识库范围内的最新可检索版本召回、有限历史追问改写，以及带稳定引用或明确拒答的 SSE
回答链路。回答决策由有界 LangGraph 状态机编排，最多执行一次补充检索和一次引用修复。
仓库同时提供版本化评测数据契约、确定性离线重放和必须显式确认的真实供应商评测入口，
分别输出检索、引用、拒答和端到端结果。`evals/datasets/agentic-rag-foundations-v1.json`
包含首个由用户逐条审核的 30 条正式评测样本。该数据集已在多个固定代码和模型配置上完成
真实供应商评测及回答结果的二次人工审核；最新正式记录使用提交 `b87c635`、
`deepseek-v4-flash`、本地 BGE-M3 与生产 BGE reranker，详见 `docs/verification.md`。业务功能按 `docs/roadmap.md`
分阶段实现，效果数字必须同时注明数据集、提交和配置，不能把单次评测泛化为产品准确率。
大模型或自动化代理开始修改前必须阅读
[`AGENTS.md`](AGENTS.md)。
