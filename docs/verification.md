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
uv run --project apps/api --extra cpu python apps/api/scripts/verify_mvp.py `
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

仓库提交版本化评测数据集、人工 judgment 及原始报告 SHA-256，但不提交包含本地论文摘录和
模型输出的 case 级报告。要独立重放真实评测，需保留三篇固定的本地论文版本、对应 ingestion
provenance、本地 BGE-M3 和有效的 OpenAI-compatible 供应商配置；缺少这些本地输入时只能核对
已提交的配置、数据集、judgment 和聚合结果，不能复现 case 级输出。

2026-08-01 在同一 Compose 环境上使用增强后的脚本重新执行完整旅程，报告为
`output/verification/mvp-strict-20260801.json`。本次额外断言：最终答案和其唯一引用摘录均
包含合成 PDF 中的唯一事实 `37 days`；拒答代码必须为 `INSUFFICIENT_EVIDENCE`；取消端点先
返回 `cancel_requested`，且取消 run 的历史记录没有答案或引用。所有断言通过，清理仍返回
HTTP 204。

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

用户已逐条审核 `evals/candidates/agentic-rag-foundations-v1.md` 中的 30 条中文问题、参考答案、
拒答标签、页码和证据摘录。审核结果已转换为正式数据集
`evals/datasets/agentic-rag-foundations-v1.json`，状态为 `reviewed`，固定上述三个不可变文档
版本，并覆盖 direct、multi_chunk、confusing 和 unanswerable。数据集通过项目 Pydantic
契约校验；47 条回答证据也已逐条确认是对应文档版本、页码中实际 chunk 文本的子串。

### 真实供应商评测

2026-07-30 使用提交 `d1261fa0ee74ac4ebc9c8e5262183ed43fa3dde1`、`gpt-5.6-luna`、
本地 BGE-M3 和数据集 `agentic-rag-foundations@1.0.0` 完成 30 题真实评测。运行耗时约
475 秒；原始待审报告 SHA-256 为
`186a5ab703bc3d3e3cb149a02d916db4ef14828ee84b2b58437d1114a04a633a`。用户将唯一待审样本
`ARF-017` 判定为通过后，生成 reviewed report，其 SHA-256 为
`88084fe0b28e06f00f02a6f6c24c71404ec02133a3e83a975312f23f4c29a795`。

| 维度 | 结果 |
|---|---|
| 检索 | 17 passed，10 failed，3 not applicable |
| 引用 | 1 passed，26 failed，3 not applicable |
| 拒答 | 3 passed，0 failed，27 not applicable |
| 端到端 | 4 passed，26 failed，0 pending review |

27 个应回答样本中只有 3 个生成答案，24 个被错误拒答。其中 14 个样本已通过检索召回，
但仍被证据充分性判断拒答；另外 10 个样本未通过检索召回。因此当前首要效果问题是证据
充分性判断的召回过低，其次才是检索召回；引用维度的大量失败也主要随错误拒答产生。上述
数字只描述该数据集、提交和固定配置，不能泛化为其他知识库上的产品准确率。

### Issue #29 引用决策修复

2026-08-02 使用提交 `0f3bcb8`、`gpt-5.6-luna`、本地 BGE-M3 和同一版本化数据集重新完成
30 题真实评测。原始报告 SHA-256 为
`48597b93e73b610cd2f5d3e491babf26e3d403a7504c64a6d7cc158409577b33`；用户审核了全部五个
待审样本 `ARF-002`、`ARF-003`、`ARF-013`、`ARF-014` 和 `ARF-017`，均判定通过。reviewed
report 的 SHA-256 为 `72738f24653e850cc73ed9434809e6b2ebdad28433786df4164f5f5736f485cc`。

本次改动只增强了生成与引用修复提示词对机器可验证 `[citation_id]` 格式的明确要求，并记录
不暴露答案正文的引用校验类别；没有降低引用校验、检索阈值或知识库范围限制。此前 14 个
“检索成功但拒答”的固定 cohort 中，本轮有 9 个回答、5 个仍拒答，满足该 Issue 的“少于 14 个”
验收条件。三个预期拒答样本仍均为拒答。

| 维度 | 结果 |
|---|---|
| 检索 | 17 passed，10 failed，3 not applicable |
| 引用 | 5 passed，22 failed，3 not applicable |
| 拒答 | 3 passed，0 failed，27 not applicable |
| 端到端 | 8 passed，22 failed，0 pending review |

引用和端到端仍有大量失败，且 5 个目标样本继续拒答，因此不能将本次结果表述为准确率提升或
视为评测问题已经解决。后续工作应保持同一数据集和人工审核流程，分别处理残余的引用格式
失败与 10 个未命中检索样本。

### Issue #31 页边界证据扩展

提交 `86715f4` 在 dense top-8 初始候选不变的前提下，为命中候选补入同一文档版本相邻一页的
chunk，并保留原始候选的分数来源。这用于处理答案证据恰好跨页、但单页 chunk 边界将其拆开的
情形；它不改变知识库范围、拒答条件、引用校验或向模型提供无引用的事实。改动还增加了去敏化
的 `diagnose-retrieval` 输出，以区分 embedding 检索弱点和后续回答策略问题。

对 Issue #31 开始时固定的 10 条 raw retrieval 未命中样本，使用相同本地 BGE-M3、数据库快照
和 `pgvector-cosine-page-context-v2` 配置重放后，未命中数为 8。这个受限回放只验证该 cohort 的
检索行为，不能等同于端到端正确率。

2026-08-04 使用同一正式数据集、本地 BGE-M3 与 `gpt-5.6-luna` 完成一次真实供应商评测。原始
报告为 `agentic-rag-foundations-v1-86715f4-attempt3.json`，SHA-256 为
`155fdff9d85e8a1b28b73c6c0336b12c7a87e664114a76ab84a724228027b9c9`。用户审核全部六个待审
样本 `ARF-002`、`ARF-003`、`ARF-005`、`ARF-013`、`ARF-014` 和 `ARF-016`，均判定通过；版本化
judgment 位于 `evals/judgments/agentic-rag-foundations-v1-86715f4.json`，其绑定后的 reviewed
report SHA-256 为 `cef15a9c2a9a3a4dccd884a1d51d9173c8b5924c515f736c1aa05f2b4d84b4a7`。

| 维度 | 结果 |
|---|---|
| 检索 | 18 passed，9 failed，3 not applicable |
| 引用 | 6 passed，21 failed，3 not applicable |
| 拒答 | 3 passed，0 failed，27 not applicable |
| 端到端 | 9 passed，21 failed，0 pending review |

该真实运行不是与前一报告完全受控的 A/B 对照：它仍有 9 条 embedding 检索弱点，其中包括此前
未列入 raw-miss cohort 的 case。因此不得把聚合数字表述为整体效果提升。它证明了评测、人工
审核和报告 SHA 绑定链路可重放，也明确留下了后续分别优化检索和证据充分性判断的失败样本。
原始 case 级报告包含本地摘录和模型输出，继续保留在被 Git 忽略的 `output/evals/` 目录，不提交。

### Issue #33 DeepSeek 兼容与供应商超时边界

提交 `285a06d` 为 OpenAI-compatible 适配器补充了覆盖整个响应生命周期的超时、结构化 JSON
响应、可配置 thinking 模式，以及只允许在首个 SSE 文本增量前执行一次的空流重连。2026-08-04
使用同一正式数据集、本地 BGE-M3、`deepseek-v4-pro` 和
`pgvector-cosine-page-context-v2` 完成真实供应商评测。原始报告 SHA-256 为
`0c25921f35ea72c89dada4f922e3443bddea95b61261d5a03855c0a52c093d15`。

用户逐条审核全部 14 个待审样本，均判定通过。版本化 judgment 位于
`evals/judgments/agentic-rag-foundations-v1-285a06d-deepseek-v4-pro.json`；绑定后的 reviewed
report SHA-256 为 `70bdb2f3d9b92a10e6150bace7873cd33c3009c261bf2986484582cc8c5f0080`。

| 维度 | 结果 |
|---|---|
| 检索 | 19 passed，8 failed，3 not applicable |
| 引用 | 14 passed，13 failed，3 not applicable |
| 拒答 | 3 passed，0 failed，27 not applicable |
| 端到端 | 17 passed，13 failed，0 pending review |

本次运行同时改变了模型供应商、结构化响应配置和引用覆盖率评测语义，因此不是与此前
`gpt-5.6-luna` 报告的受控单变量 A/B 对照。结果只能描述该数据集、提交和固定配置；它不能
泛化为产品准确率，也不能单独证明 DeepSeek 优于其他模型。当前仍有 8 个应回答样本未通过
检索召回，另有已生成回答未满足版本化预期证据覆盖，后续应继续按失败类型分别诊断。

## 6. 后续评测工作

1. 使用 `diagnose-retrieval` 对剩余 embedding 检索弱点逐例分析，独立处理文档切分、查询和召回问题。
2. 根据失败 case 分析证据充分性提示词、阈值和选择策略，不删除或弱化现有评测样本。
3. 每次调整后使用同一版本化数据集重新运行真实评测，并生成绑定新报告 SHA-256 的 judgments。
4. 简历只能引用 reviewed report 的限定结果，并同时说明发现的问题和后续优化方向。
