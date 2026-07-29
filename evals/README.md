# Evals

这里存放去敏、版本化的 RAG 评测数据与 JSON Schema。大型原始文档、用户数据、供应商响应
缓存和临时报告不提交到 Git；本地报告统一写入被忽略的 `output/evals/`。

## 数据状态

`fixtures/` 中的四类样例只验证评测工具链，覆盖直接命中、多片段综合、无法回答和高混淆
问题。它们使用合成 UUID，`review.status` 为 `fixture`，不能作为产品指标，也不能用于真实供应商
评测。正式数据集目标仍为约 30 条基于演示知识库、由用户人工审核的样本；审核时必须填写
审核人和带时区时间，并引用真实的不可变文档版本、页码和证据摘录。

## 离线重放

```powershell
pnpm generate:eval
pnpm eval:fake
```

`generate:eval` 从 Pydantic 契约重新生成 `schema/`；`eval:fake` 使用独立的确定性 observation
重放 fixture，并输出四组分离结果：检索召回、引用正确性、拒答行为和端到端结果。回答样本的
端到端结果在没有人工 judgment 时保持 `pending_review`。

## 真实受控评测

真实评测要求 PostgreSQL 中存在数据集指定的知识库和文档版本，本机可加载配置的 embedding
模型，并已在 `.env` 配置 OpenAI-compatible 供应商。命令必须显式提供当前代码提交和确认参数：

```powershell
uv run --project apps/api python -m sourcetrace.evaluation.cli real `
  --dataset evals/datasets/<dataset>.json `
  --code-commit (git rev-parse HEAD) `
  --output output/evals/<report>.json `
  --confirm-real-provider
```

真实命令拒绝 `fixture` 数据集。常规 `pnpm test` 不执行该命令，不访问数据库、embedding 模型或
LLM API。报告绑定数据集、代码、模型、工作流、切分、embedding 和检索配置版本；未实际运行
并完成必要人工审核时，不得把报告数字写入 README、简历或项目说明。
