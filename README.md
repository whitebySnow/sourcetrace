# SourceTrace

SourceTrace 是一个强调证据可定位、回答可追溯和证据不足时拒答的 RAG 知识库应用。

当前仓库采用模块化单体架构：FastAPI API 与异步 Worker 共享 Python 领域代码，Vue 3 前端通过 OpenAPI 契约访问后端，PostgreSQL/pgvector 保存业务数据与向量，Redis 承担任务队列和短期缓存。

## 快速开始

前置条件：Python 3.12+、uv、Node.js 22+、pnpm 10+ 和 Docker。

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
uv sync --project apps/api --all-groups
pnpm install
pnpm dev
```

API 默认运行在 `http://localhost:8000`，Web 默认运行在 `http://localhost:5173`。健康检查为 `GET /health`，依赖就绪检查为 `GET /ready`，OpenAPI 文档为 `/docs`。

首次处理文档时，Worker 会通过配置的 Hugging Face 镜像下载 BGE-M3 到宿主机
`D:\DevelopEnvironment\huggingface`。模型缓存不在仓库内，也不会写入 Docker 镜像；详细的
镜像切换、本地模型目录和设备配置见 [开发约定](docs/development.md)。

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
```

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
分别输出检索、引用、拒答和端到端结果。当前四类 fixture 只验证工具链，正式约 30 条人工
审核数据集仍需基于最终演示知识库整理，不能把 fixture 结果当作产品指标。业务功能按
`docs/roadmap.md` 分阶段实现，所有效果指标必须来自实际运行的版本化评测集，不填写虚构
数值。大模型或自动化代理开始修改前必须阅读
[`AGENTS.md`](AGENTS.md)。
