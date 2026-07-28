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

仓库已具备知识库、不可变文档版本、异步 PDF 解析切分和本地 dense embedding 闭环。
业务功能按 `docs/roadmap.md` 分阶段实现，所有效果指标必须来自版本化评测集，不填写
虚构数值。大模型或自动化代理开始修改前必须阅读
[`AGENTS.md`](AGENTS.md)。
