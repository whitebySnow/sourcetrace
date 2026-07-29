# Agentic RAG Foundations 候选评测题单 v1

本文件是人工审核工作表，不是 `EvaluationDataset`，不得传给 `real` 评测命令。以下问题、
参考答案和证据定位均由模型起草，`reviewed_by`、`reviewed_at` 和最终证据摘录必须由用户本人
核验后填写。

## 固定候选语料

- 知识库：`Agentic RAG Foundations`
- 知识库 ID：`c5557289-002a-4631-86c2-f53bfec90706`
- RAG：`rag-2020.pdf`，版本 `c9b7c41e-4716-403f-a115-3cdaa3a86741`，16 页，44 chunks
- ReAct：`react-2023.pdf`，版本 `eb8f384b-d03b-42da-b813-4dedc1c39760`，33 页，90 chunks
- Self-RAG：`self-rag-2024.pdf`，版本 `41f20261-5c9e-4856-a5ff-9dc37da4203d`，30 页，73 chunks
- 摄取配置：`pypdf-v2`、`token-window-v1`、本地 BGE-M3，三个 run 均为首次完成

论文来自 NeurIPS、arXiv 的作者公开版本。PDF 只保存在本地运行数据中，不提交到仓库。

## 审核规则

逐行打开 PDF 原页，检查问题是否清楚、参考答案是否只依赖指定语料、页码是否正确。回答类
问题还要从页面复制最小且完整的证据摘录；拒答类问题必须确认三篇论文均没有答案。只有全部
完成后，才能生成 `evals/datasets/*.json` 并填写真实审核人和 UTC 时间。

`审核` 一栏由用户改为 `通过` 或 `修改后通过`，不能由模型代填。

## 候选问题

| ID | 类别 | 问题 | 参考答案草稿 | 证据定位草稿 | 审核 |
|---|---|---|---|---|---|
| ARF-001 | direct | 原始 RAG 论文把哪两类记忆结合起来？ | 预训练模型的参数化记忆与外部索引提供的非参数化记忆。 | RAG，第 1 页，摘要中对两类 memory 的定义。 | 待审核 |
| ARF-002 | direct | RAG 论文分别使用什么模型作为检索器和生成器？ | 检索器基于 DPR，生成器使用 BART-large。 | RAG，第 2-3 页，Retriever: DPR 与 Generator: BART。 | 待审核 |
| ARF-003 | confusing | RAG-Sequence 与 RAG-Token 对检索文档的使用方式有什么区别？ | 前者对整个输出序列使用同一潜在文档；后者可为不同目标 token 使用不同潜在文档。 | RAG，第 3 页，两种模型定义。 | 待审核 |
| ARF-004 | direct | RAG 如何从文档索引中找到 top-K 文档？ | 把查询和文档编码为稠密向量，并用最大内积搜索近似求解 top-K。 | RAG，第 2-3 页，MIPS 与 DPR bi-encoder。 | 待审核 |
| ARF-005 | direct | 原始 RAG 训练时是否更新文档编码器？ | 不更新；论文固定文档编码器和索引，只微调查询编码器与 BART 生成器。 | RAG，第 4 页，Training。 | 待审核 |
| ARF-006 | direct | RAG 论文在哪些开放域问答数据集上报告了领先结果？ | Natural Questions、WebQuestions 和 CuratedTREC。 | RAG，第 2 页，Introduction 的结果概述。 | 待审核 |
| ARF-007 | direct | RAG 的非参数化记忆为什么更容易更新？ | 可以在测试时替换文档索引，无需像纯参数模型那样重新训练模型参数。 | RAG，第 7-8 页，Index hot-swapping。 | 待审核 |
| ARF-008 | direct | Jeopardy 生成任务的人类评估如何比较 RAG 与 BART 的事实性？ | 评估者判定 RAG 更事实正确的比例显著高于判定 BART 更好的比例。 | RAG，第 6-8 页，Jeopardy 人工评估与 Table 4。 | 待审核 |
| ARF-009 | direct | ReAct 的核心生成模式是什么？ | 在同一轨迹中交错生成语言推理轨迹和任务动作。 | ReAct，第 1 页，摘要。 | 待审核 |
| ARF-010 | direct | ReAct 中推理轨迹和动作分别提供什么帮助？ | 推理用于维护计划、处理异常；动作用于和外部环境交互并获取新信息。 | ReAct，第 1 页，摘要。 | 待审核 |
| ARF-011 | direct | ReAct 的动作可以从哪些外部来源获取信息？ | 知识库或环境等外部来源。 | ReAct，第 1 页，摘要。 | 待审核 |
| ARF-012 | direct | ReAct 在论文中用于哪两个知识型任务？ | HotpotQA 问答和 FEVER 事实核验。 | ReAct，第 1 页，摘要。 | 待审核 |
| ARF-013 | direct | ReAct 在论文中用于哪两个交互式决策基准？ | ALFWorld 和 WebShop。 | ReAct，第 1 页及第 7 页，摘要与实验结果。 | 待审核 |
| ARF-014 | direct | ReAct 在 ALFWorld 上相对 Act 和 BUTLER 的最好结果如何？ | 最佳 ReAct 成功率为 71%，高于最佳 Act 的 45% 和 BUTLER 的 37%。 | ReAct，第 7 页，ALFWorld Results。 | 待审核 |
| ARF-015 | confusing | ReAct 与只执行动作的 Act 基线差异是什么？ | ReAct 在动作之间保留语言推理轨迹；Act 只产生动作，因此缺少显式计划和状态推理。 | ReAct，第 7-8 页，结果与消融分析。 | 待审核 |
| ARF-016 | direct | ReAct-IM 在 ALFWorld 中暴露了哪些典型问题？ | 它容易误判子目标是否完成、下一子目标是什么，并缺少定位物品所需的常识推理。 | ReAct，第 8 页，ReAct-IM 分析。 | 待审核 |
| ARF-017 | direct | Self-RAG 认为传统固定数量检索有什么问题？ | 不论是否需要或是否相关都检索固定数量段落，可能引入无关信息并降低生成质量与灵活性。 | Self-RAG，第 1 页，摘要与 Introduction。 | 待审核 |
| ARF-018 | direct | Self-RAG 的 reflection tokens 用来做什么？ | 控制是否检索，并评价检索段落、生成内容的支持度与整体效用。 | Self-RAG，第 3-4 页，框架概述与 Table 1。 | 待审核 |
| ARF-019 | multi_chunk | Self-RAG 定义了哪四类 reflection token，各自判断什么？ | Retrieve 判断是否检索；ISREL 判断段落相关性；ISSUP 判断回答受证据支持程度；ISUSE 判断回答效用。 | Self-RAG，第 3-4 页，跨相邻 chunks 核对 Table 1 与正文。 | 待审核 |
| ARF-020 | multi_chunk | Self-RAG 一次需要检索时，生成和自评流程如何进行？ | 先触发检索，再评估候选段落相关性，生成下一段回答，检查该段是否受证据支持，最后评估整体效用。 | Self-RAG，第 3-4 页，问题形式化、推理流程与 Algorithm 1。 | 待审核 |
| ARF-021 | direct | Self-RAG 如何实现按需检索？ | 模型先生成检索决策 token；只有判断需要外部知识时才调用检索器。 | Self-RAG，第 2-4 页，Retrieve on demand 与推理说明。 | 待审核 |
| ARF-022 | direct | Self-RAG 的 reflection tokens 在推理阶段如何控制输出？ | 它们可作为软重排分数或硬约束，按任务需求调整检索与候选生成选择。 | Self-RAG，第 4 页及第 6 页，Inference。 | 待审核 |
| ARF-023 | direct | Self-RAG 是否承诺引用一定完全支持输出？ | 不承诺。论文明确指出即使有改进，输出仍可能没有被引用完全支持。 | Self-RAG，第 11 页，Ethical Concerns。 | 待审核 |
| ARF-024 | multi_chunk | 原始 RAG 与 Self-RAG 的检索时机有何主要差异？ | 原始 RAG 对输入取 top-K 文档参与生成；Self-RAG 先判断是否需要检索，并可在分段生成中按需触发。 | RAG，第 2-3 页；Self-RAG，第 1-4 页。 | 待审核 |
| ARF-025 | multi_chunk | ReAct 与 Self-RAG 分别如何利用模型生成的中间信号？ | ReAct 交错生成推理轨迹和环境动作；Self-RAG 生成检索与批评 token 来选择证据并自评输出。 | ReAct，第 1 页；Self-RAG，第 3-4 页。 | 待审核 |
| ARF-026 | confusing | DPR、BART、环境动作和 critique token 分别属于哪篇论文的哪个组件？ | DPR 与 BART 是原始 RAG 的检索器和生成器；环境动作属于 ReAct；critique token 属于 Self-RAG。 | RAG，第 2-3 页；ReAct，第 1 页；Self-RAG，第 3-4 页。 | 待审核 |
| ARF-027 | unanswerable | SourceTrace 如何通过 SSE 取消正在生成的回答？ | 应拒答；三篇论文没有 SourceTrace 的实现细节。 | 固定语料快照内无证据，evidence 必须为空。 | 待审核 |
| ARF-028 | unanswerable | GPT-5.6-luna 当前每百万 token 的价格是多少？ | 应拒答；三篇论文不包含该模型或实时价格。 | 固定语料快照内无证据，evidence 必须为空。 | 待审核 |
| ARF-029 | unanswerable | 三篇论文作者在 2026 年各自任职于哪家公司？ | 应拒答；论文只反映发表时署名，不能证明 2026 年任职情况。 | 固定语料快照内无可支持的时效证据，evidence 必须为空。 | 待审核 |
| ARF-030 | confusing | “Self-RAG 已彻底解决引用不支持回答的问题”这一说法正确吗？ | 不正确；论文报告改进，但明确保留输出未被引用完全支持的风险。 | Self-RAG，第 11 页，Ethical Concerns；与第 1-2 页的改进主张对照。 | 待审核 |

## 审核完成后

1. 把每个回答类 case 的最终答案和逐字证据摘录写入正式 JSON。
2. 确认所有 `document_version_id` 与上方固定快照一致。
3. 将 `review.status` 设为 `reviewed`，填写用户身份和真实 UTC 时间。
4. 先校验 `dataset-v1.schema.json`，再运行一次真实供应商评测。
5. 对待审报告逐条作出 judgment；未完成这一步前不统计端到端通过率。
