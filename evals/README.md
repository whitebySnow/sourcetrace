# Evals

这里存放去敏、版本化的 RAG 评测数据与 JSON Schema。大型原始文档、用户数据、供应商响应
缓存和临时报告不提交到 Git；本地报告统一写入被忽略的 `output/evals/`。

## 数据状态

`fixtures/` 中的四类样例只验证评测工具链，覆盖直接命中、多片段综合、无法回答和高混淆
问题。它们使用合成 UUID，`review.status` 为 `fixture`，不能作为产品指标，也不能用于真实供应商
评测。正式数据集目标仍为约 30 条基于演示知识库、由用户人工审核的样本；审核时必须填写
审核人和 UTC 时间，并引用真实的不可变文档版本、页码和证据摘录。
数据集顶层 `document_version_ids` 固定评测语料快照；所有证据必须属于该集合，无法回答的
case 也因此能在文档更新后稳定重放。

`candidates/` 只存放人工审核工作表。它不是评测输入，模型起草的参考答案、页码和核验要点
不构成人工真值，也不能填写 `reviewed` 元数据。用户完成逐条核验后，才把结果转换为
`evals/datasets/` 中符合 Schema 的正式数据集。当前
`datasets/agentic-rag-foundations-v1.json` 已完成 30 条逐项审核，固定三篇论文版本并覆盖
direct、multi_chunk、unanswerable 和 confusing 四类样本；其中不可回答样本的 evidence 为空。

## 离线重放

```powershell
pnpm generate:eval
pnpm eval:fake
```

`generate:eval` 从 Pydantic 契约重新生成 `schema/`；`eval:fake` 使用独立的确定性 observation
重放 fixture，并输出四组分离结果：检索召回、引用正确性、拒答行为和端到端结果。回答样本的
端到端结果在没有人工 judgment 时保持 `pending_review`。

回答 case 的人工判定使用 `judgments-v1.schema.json`，必须绑定相同 dataset ID/version、待审
报告文件的 SHA-256、审核人与 UTC 时间，并完整覆盖所有 `pending_review` case。审核只通过
独立的 `review` 命令应用到既有报告，不能在新一轮模型调用前复用旧 judgment。

## 真实受控评测

真实评测要求 PostgreSQL 中存在数据集指定的知识库和文档版本，本机可加载配置的 embedding
模型，并已在 `.env` 配置 OpenAI-compatible 供应商。命令必须显式提供当前代码提交和确认参数：

```powershell
uv run --project apps/api --extra cpu python -m sourcetrace.evaluation.cli real `
  --dataset evals/datasets/<dataset>.json `
  --code-commit (git rev-parse HEAD) `
  --output output/evals/<report>.json `
  --confirm-real-provider
```

对该报告的回答逐条人工核对后，把原始报告文件 SHA-256 写入 judgment 文件，再离线应用：

```powershell
$reportHash = (Get-FileHash output/evals/<report>.json -Algorithm SHA256).Hash.ToLowerInvariant()
pnpm eval:review -- `
  --report output/evals/<report>.json `
  --judgments evals/judgments/<judgments>.json `
  --output output/evals/<reviewed-report>.json
```

真实命令拒绝 `fixture` 数据集，并把 pgvector 查询严格限制在数据集的文档版本快照。它从
这些版本实际产生 chunk 的 completed ingestion run 读取切分参数及 embedding 模型、revision
和配置版本；快照缺失、不可检索、provenance 不完整或混用配置时直接失败。常规 `pnpm test`
不执行该命令，不访问数据库、embedding 模型或 LLM API。报告绑定数据集、代码、模型、
parser、切分、embedding、四个 prompt、工作流和检索参数/版本；未实际运行
并完成必要人工审核时，不得把报告数字写入 README、简历或项目说明。

## 离线混合检索实验

`query-plans/` 存放人工版本化的有界查询计划，只能增加由原问题直接派生的检索表达，不能写入
答案、证据内容或人工不可见的目标词。混合检索实验使用 PostgreSQL 英文全文检索与 dense
检索做 RRF 融合，然后复用生产 reranker、分页多样性和相邻页扩展。它不调用远程 LLM，也不
改变生产检索路径：

```powershell
$env:EMBEDDING_DEVICE = "cuda"
$env:RERANKER_DEVICE = "cuda"
uv run --project apps/api --extra cu130 python -m sourcetrace.evaluation.cli hybrid-retrieval `
  --dataset evals/datasets/agentic-rag-foundations-v1.json `
  --query-plan evals/query-plans/agentic-rag-foundations-v1-bounded-counterexample-v3.json `
  --code-commit (git rev-parse HEAD) `
  --output output/evals/hybrid-retrieval-report.json `
  --confirm-local-model
```

报告逐题记录 dense、lexical、通道融合和 reranker 排名，但不复制文档正文。任何线上接入、索引
迁移或阈值调整都必须另开 Issue，并以本实验报告作为决策输入。没有可用 NVIDIA GPU 时可移除
上述两个临时环境变量并改用 `--extra cpu`，但完整 30 题双路径重排会明显更慢。
