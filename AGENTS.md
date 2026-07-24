# SourceTrace Agent Guide

本文件约束所有在本仓库中工作的开发者和代码生成模型。开始修改前，先阅读
`README.md`、`docs/architecture.md` 和本文件。

## Product invariant

SourceTrace 的核心承诺不是“总能回答”，而是：

1. 每个答案声明都能定位到具体证据片段。
2. 检索证据不足时明确拒答，不编造来源。
3. 文档解析、切分、嵌入和回答过程可重放、可评测。

任何绕过引用、将模型输出直接当作事实或伪造评测指标的实现都不可接受。

## Architecture

- 仓库采用模块化单体；不要在没有独立扩缩容或所有权需求前拆微服务。
- 后端按业务功能组织在 `apps/api/src/sourcetrace/modules/<feature>/`。
- 每个业务模块按 `router -> service -> repository` 单向依赖。
- `router.py` 只处理 HTTP、校验和依赖注入，禁止包含业务规则和 SQL。
- `service.py` 负责用例编排和事务边界，禁止依赖 FastAPI 请求/响应类型。
- `repository.py` 负责持久化，禁止包含回答策略等业务规则。
- 外部模型、向量库、对象存储和队列通过 Protocol/port 接口接入。
- `core/` 只放横切能力，`db/` 只放数据库基础设施，禁止形成杂物目录。
- Web 按 `features/<feature>` 组织；跨业务复用代码放 `shared/`。
- API 和 Worker 是不同进程，共享应用层代码；后台任务必须幂等。

## Backend module template

只有实际需要时才创建文件，禁止先生成空层：

```text
modules/<feature>/
  __init__.py
  models.py       # SQLAlchemy persistence model
  schemas.py      # Pydantic boundary DTO
  repository.py   # database access
  service.py      # use cases and business rules
  router.py       # FastAPI transport adapter
  dependencies.py # dependency wiring, when non-trivial
```

模块间不得直接导入对方的 repository；通过公开 service 或 port 协作。

## Frontend feature template

```text
features/<feature>/
  api/            # typed endpoint calls
  components/     # feature-specific UI
  composables/    # feature state and workflows
  pages/          # route-level views
  types.ts        # UI-only types, not duplicated API contracts
  __tests__/
```

OpenAPI 类型由后端契约生成，禁止手写一份同名响应模型。所有请求必须经过
`shared/api/client.ts`，页面和组件不能散落 `fetch` 调用。

## Engineering rules

- Python 3.12+，完整类型标注；通过 Ruff 和 mypy strict。
- TypeScript 开启 strict；Vue 组件使用 Composition API 和 `<script setup>`。
- 配置只从 `core/config.py` 或前端环境模块读取；不得散落读取环境变量。
- 所有 API 输入在边界验证；所有错误使用统一问题详情结构并携带请求 ID。
- 不记录密码、令牌、完整文档内容和其他敏感数据。
- 数据库变更必须通过 Alembic；生产迁移采用增量、可回滚步骤。
- 列表接口必须分页；公开资源使用 UUID；时间使用带时区 UTC。
- 缓存必须设置 TTL，Redis 不能作为权威数据源。
- 新依赖必须说明现有标准库或依赖为何不能解决问题。
- 不提交 `.env`、用户上传、模型密钥、数据库数据或构建产物。

## Markdown math

编辑 Markdown 时，行内公式使用 `$...$`，独立公式使用 `$$...$$`。保留原公式的
希腊字母、上下标、范数和 `\tag{}` 编号；不得用 ASCII 近似数学公式，也不得把
公式放进代码块，除非用户明确要求展示原始 LaTeX。

## Testing

- `tests/unit/`：纯业务逻辑，无网络和数据库。
- `tests/integration/`：真实 API/数据库边界，外部模型使用可控 fake。
- `tests/contract/`：OpenAPI、解析器和模型供应商适配器契约。
- 每个修复至少包含一个先前会失败的回归测试。
- 测试断言外部可观察行为，不锁定内部实现。
- 不得删除、跳过或弱化测试来让检查通过。

## Commands

```powershell
pnpm install
uv sync --project apps/api --all-groups
pnpm check
pnpm test
pnpm build
```

提交前至少运行与改动范围相称的检查。若因环境原因无法运行，在交付说明中明确列出。

## Change discipline

- 保持改动聚焦，不顺手重构无关代码。
- 修改 API 契约后重新生成并提交前端类型。
- 修改架构边界时同步更新 `docs/architecture.md`，重要取舍写入 `docs/adr/`。
- 不填写未经版本化评测集运行得到的准确率、召回率、延迟等数字。
- 遇到需求不明确时，优先实现可逆的最小纵向切片，并记录假设。

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses the single-context domain documentation layout. See
`docs/agents/domain.md`.
