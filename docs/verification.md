# SourceTrace MVP 验收记录

## 1. 记录范围

本记录对应 2026-07-29 的本地 CPU Compose 验收。运行中的应用代码基线为 Git 提交
`095ae88bf387aa4931280fd81ca4c31fbbc9f161`。验收使用合成 PDF 和独立测试知识库，结束后通过
公开删除接口清理，不读取或修改用户知识库。

本记录只陈述实际执行结果。它不提供公开托管演示或演示视频，也不把单次 smoke test 当作
性能测试或 RAG 效果指标。

## 2. 环境

| 组件 | 实际版本或配置 |
|---|---|
| Docker Client / Server | 29.6.1 / 29.6.1 |
| Docker Compose | 5.3.0 |
| uv | 0.10.0 |
| Node.js | 24.18.0 |
| pnpm | 11.14.0 |
| 部署方式 | `compose.yaml`，CPU Worker |
| Embedding | 挂载的 BGE-M3 本地缓存，不写入镜像 |
| 回答模型 | `.env` 配置的 OpenAI-compatible 远程模型 |

验收时 Web、API、Worker、PostgreSQL 和 Redis 均在 Compose 中运行；API、Web、PostgreSQL
和 Redis 健康检查通过，Alembic 迁移容器以退出码 0 完成。

## 3. 完整用户旅程

执行命令：

```powershell
uv run --project apps/api python apps/api/scripts/verify_mvp.py `
  --output output/verification/issue-14.json
```

脚本仅调用公开 HTTP/SSE 契约，不导入 Service 或 Repository 绕过边界。实际结果如下：

| 步骤 | 结果 |
|---|---|
| 创建知识库 | 通过 |
| 上传文本型 PDF | HTTP 202 |
| Redis 队列与 Worker 摄取 | `pending:queued` -> `processing:embedding` -> `completed:completed` |
| 知识库范围检索与回答 | SSE 以 `final` 结束 |
| 引用约束 | 1 条引用，指向本次不可变文档版本第 1 页 |
| 引用源访问 | PDF 源接口 HTTP 200 |
| 证据不足 | SSE 以 `refusal` 结束，代码为 `INSUFFICIENT_EVIDENCE` |
| 主动取消 | 请求状态为 `cancel_requested`，SSE 以 `cancelled` 结束 |
| 回答历史 | 2 个 `completed`、1 个 `cancelled`；结果含 `answered` 与 `refused` |
| 问题历史 | 3 条问题均保留 |
| 清理 | 删除测试知识库返回 HTTP 204 |

该运行不保存答案正文、完整 PDF 内容、提示词或模型密钥。原始去敏 JSON 位于被 Git 忽略的
`output/verification/`，不作为仓库内长期结果。

## 4. 工程质量门禁

以下命令均在相同工作区实际运行：

| 命令 | 结果 |
|---|---|
| `pnpm generate:api` + 生成文件差异检查 | 通过，生成契约稳定 |
| `pnpm generate:eval` + Schema 差异检查 | 通过，生成契约稳定 |
| `pnpm check` | 通过：Ruff、mypy strict、Vue TypeScript |
| `pnpm test` | 通过：后端 148 项、Web 27 项 |
| `pnpm build` | 通过：Web 生产构建完成 |
| `docker compose config --quiet` | 通过：CPU 配置有效 |
| GPU override 配置检查 | 通过；本次没有声称在 NVIDIA 硬件上实际运行 |

测试出现两个第三方弃用警告：Starlette TestClient 的 httpx 接口迁移提示，以及 LangGraph
serializer 默认值未来变化提示。它们没有被隐藏或计为失败，后续应随依赖升级单独处理。

## 5. 评测记录

执行 `pnpm eval:fake` 后，确定性 fixture 报告为：

| 维度 | 结果 |
|---|---|
| 检索 | 3 passed，1 not applicable |
| 引用 | 2 passed，1 failed，1 not applicable |
| 拒答 | 1 passed，3 not applicable |
| 端到端 | 1 passed，1 failed，2 pending review |

fixture 中故意放入一条指向干扰文档的越界引用，因此引用与端到端各出现一次失败。该结果
证明评测器能识别错误，不代表 SourceTrace 的产品准确率、召回率或通过率。

### 候选真实语料

已在本地创建 `Agentic RAG Foundations` 知识库，并固定三篇作者公开论文版本：原始 RAG、
ReAct 和 Self-RAG。三个版本分别为 16、33 和 30 页，产生 44、90 和 73 个 chunks；均使用
`pypdf-v2`、`token-window-v1` 和同一本地 BGE-M3 配置首次摄取完成。PDF、向量和运行数据不
提交到 Git。

仓库中的 `evals/candidates/agentic-rag-foundations-v1.md` 提供 30 条中文候选问题，覆盖
direct、multi_chunk、confusing 和 unanswerable。它只是模型起草的人工审核工作表，不是
`EvaluationDataset`，也没有 `reviewed` 状态。

真实供应商正式评测仍未运行。原因是候选题的参考答案、页码和逐字证据尚未由用户逐条审核。
模型不能替用户填写 reviewer、审核时间或期望证据。完成该人工门禁前，只能在简历和面试中
陈述“已实现版本化评测框架、真实评测入口和候选评测集”，不能声明未经运行的效果数字。

## 6. 剩余人工门禁

1. 用户确认三篇候选论文是否作为最终演示语料。
2. 用户逐条审核 30 个问题、预期回答或拒答、页码和证据摘录。
3. 将审核结果转换为正式 Dataset，状态设为 reviewed，并记录真实审核人和 UTC 时间。
4. 使用当前 Git 提交运行 `real` 评测。
5. 用户逐条审阅待审回答并应用绑定报告 SHA-256 的 judgments。
6. 只有最终 reviewed report 中的结果可以进入简历。
