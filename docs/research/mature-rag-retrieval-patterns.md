# 成熟 RAG 项目的相同故障与解决模式

## 调研问题

SourceTrace 当前剩余问题可以分为三类：

1. 直接事实题的目标片段没有进入 dense Top 8。
2. 对比、组合和反例问题需要同时召回多个证据槽位。
3. 回答可能具有合理语义，但版本化评测要求所有预期片段按文档、页码和文本子串精确命中，
   因而把检索、引用或端到端结果判为失败。

这些问题在成熟 RAG 项目中普遍存在。成熟实现通常不把它们归结为“换一个更大的 embedding
模型”，而是分别处理查询覆盖、召回通道、候选排序、上下文组织和评测语义。

本文是一份工程调研记录，不替代 `docs/specification.md`、`docs/architecture.md` 或 ADR。

## 成熟项目采用的模式

| 项目或一手资料 | 处理方式 | 对应故障 |
| --- | --- | --- |
| [LangChain MultiQueryRetriever](https://api.python.langchain.com/en/v0.1/retrievers/langchain.retrievers.multi_query.MultiQueryRetriever.html) | 用模型生成多个查询，逐个检索并返回唯一文档集合；可显式保留原查询 | 单一查询表达无法覆盖目标片段 |
| [LlamaIndex SubQuestionQueryEngine 源码](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/sub_question_query_engine.py) | 把复杂问题拆成子问题，分别执行后再综合 | 对比题和多证据组合题 |
| [Haystack DocumentJoiner](https://docs.haystack.deepset.ai/docs/documentjoiner) | 合并多个检索分支，并支持 Reciprocal Rank Fusion | dense、词法或多查询候选需要稳定融合 |
| [Haystack AutoMergingRetriever](https://docs.haystack.deepset.ai/docs/automergingretriever) | 在叶子片段命中足够多时返回更大的父文档上下文 | 目标信息跨切分边界或片段过碎 |
| [Qdrant Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/) | dense 与 sparse 多路预取，使用 RRF 或分布式分数融合；也支持先扩大候选再用多向量重排 | dense 单路漏召回和最终排序精度 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 多路召回后融合重排；提供检索测试和分块可视化 | 生产检索需要诊断、人工校正和多阶段排序 |
| [RAGFlow Quickstart 源文件](https://github.com/infiniflow/ragflow/blob/main/docs/quickstart.mdx) | 允许检查切分结果，并为片段添加关键词或问题来改善检索排名 | 文档术语、标题或切分结果难以被用户查询命中 |
| [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) | BGE-M3 本身支持 dense、sparse 和 multi-vector；官方同时提供 cross-encoder reranker | 同一模型族内增加召回通道或候选重排 |
| [Microsoft GraphRAG Query Engine](https://microsoft.github.io/graphrag/query/overview/) | 根据问题类型选择 basic、local、global 或 DRIFT；DRIFT 会生成更详细的后续问题 | 跨文档关系和全局主题问题 |
| [RAGChecker](https://github.com/amazon-science/RAGChecker) | 用 claim-level entailment 分开诊断 retriever 与 generator，并报告 claim recall、context precision 和 faithfulness | 固定片段匹配无法代表答案正确性与忠实度 |
| [Ragas metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | 分开计算 context precision、context recall、faithfulness 和 response relevancy | 不能用单一端到端状态定位故障层 |
| [TruLens RAG Triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/) | 分开检查 context relevance、groundedness 和 answer relevance，并把回答拆成声明检查证据支持 | 回答相关但不忠实，或证据相关但回答不完整 |

## 问题一：直接事实题漏召回

### 成熟项目如何处理

成熟项目通常采用逐层扩大召回再收缩候选的管线：

1. 查询改写或多查询覆盖同义表达、缩写和跨语言表达。
2. dense 与 sparse 或词法检索并行，避免精确术语完全依赖向量距离。
3. 使用 RRF 等秩融合合并不同分支，避免直接比较不同评分尺度。
4. 从较大的候选池中使用 cross-encoder 或 multi-vector reranker 选出最终上下文。
5. 若目标页已命中但片段不完整，再使用父子片段、自动合并或邻接扩展。

Qdrant 的官方混合查询文档把 dense 和 sparse 描述为语义理解与精确词匹配的互补通道，并把
候选预取与最终重排区分开。FlagEmbedding 也明确把 embedding 召回和 reranker 作为两个阶段；
reranker 只能重排已经召回的候选，不能修复目标片段从未进入候选池的问题。

### 对 SourceTrace 的判断

ARF-011 和 ARF-012 的预期证据位于 ReAct 论文第 1 页，但当前 dense Top 8 未命中该页。这是
召回覆盖问题，不应先调证据门槛或生成提示词。

推荐顺序：

1. 先完成 Issue #39，让额外查询可以生成与英文论文术语更贴近的独立检索表达。
2. 若两题仍失败，建立单独 A/B：dense-only 对比 dense 与词法或 BGE-M3 sparse 的混合召回。
3. 只有目标片段稳定进入扩大后的候选池、却无法进入最终 Top 8 时，才评估 BGE reranker。
4. 不在同一实验中同时更换 embedding、切分、召回通道和重排器。

SourceTrace 不需要为此迁移到 Qdrant 或 RAGFlow。可以在现有 PostgreSQL 与 pgvector 边界内增加
一个词法或 sparse 检索 port，再复用 Issue #39 定义的确定性 RRF。是否采用 PostgreSQL 全文
检索或 BGE-M3 sparse 输出，应由固定两题和全部 30 题的受控 A/B 决定。

## 问题二：复杂问题需要多个证据槽位

### 成熟项目如何处理

LangChain 的 MultiQueryRetriever 用多个角度改写同一个查询；LlamaIndex 的
SubQuestionQueryEngine 明确面向 compare-and-contrast 等复杂问题，将其拆成多个子问题后再
综合。Microsoft GraphRAG 的 DRIFT 也通过更详细的后续问题扩大事实覆盖，但需要额外索引、
社区摘要和多轮模型调用。

这些方案的共同点不是“让 Agent 自由循环”，而是：

- 显式保存查询计划；
- 每个子查询独立检索；
- 合并候选时保留来源与排名；
- 限制查询数量、深度和上下文预算；
- 最终仍只允许文档片段支持回答。

### 对 SourceTrace 的判断

Issue #39 的“原始问题加最多两条额外查询、最多一次补检、确定性 RRF”与成熟项目的常见做法
一致，并且比直接引入完整框架更符合当前模块化单体和可重放要求。

它优先处理 ARF-023、ARF-024、ARF-026 和 ARF-030：反例查询需要主动搜索 limitation 或
unsupported 等表述，对比和组合题则需要分别覆盖各个证据槽位。

GraphRAG 目前不应采用。当前语料只有三篇论文，目标问题主要是局部事实和有限跨文档组合；
构建知识图谱、社区报告和 global 或 DRIFT 查询的成本与新的不可确定性明显超过收益。只有未来
知识库扩大到大量互相关联文档，并出现“整体主题、关系网络、跨文档趋势”类稳定需求时再评估。

## 问题三：正确回答与严格片段评测不一致

### SourceTrace 当前判定方式

`EvaluationHarness` 当前要求每个预期证据都满足以下三个条件：

- 文档版本完全相同；
- 页码完全相同；
- 预期文本是实际证据文本的子串。

多个预期证据使用全满足语义。只要 retrieval 或 citation 任一维度失败，answered case 的
end-to-end 就直接失败。当前 dataset schema 也只能列出一组全部必需的证据，不能表达“这个
声明可以由多个等价片段中的任意一个支持”。

这种确定性检查适合验证证据定位和防止伪造，但它不能单独判断：

- 实际引用是否使用了同一论文中的另一段等价证据；
- 回答中的每个声明是否真的被引用片段支持；
- 回答是否完整覆盖参考答案；
- 检索失败与生成器未利用已召回证据之间的差异。

### 成熟项目如何处理

RAGChecker 使用 claim-level entailment，把参考答案和模型回答拆成独立声明，再分别检查参考
声明是否被检索上下文覆盖，以及回答声明是否被上下文支持。Ragas 和 TruLens同样把检索召回、
上下文相关性、回答忠实度和回答相关性拆成不同轴。

这并不意味着删除 SourceTrace 的确定性引用校验。成熟做法是同时保留两层：

1. 运行时硬门禁继续校验 Citation 指向真实、允许且不可变的 chunk。
2. 离线评测再判断引用证据对回答声明的语义支持与覆盖。

### 对 SourceTrace 的建议

评测集应升级为新的版本，而不是修改旧报告：

- 把预期证据建模为多个 evidence slot；不同 slot 必须全部满足。
- 每个 slot 支持多个 `any_of` 等价证据片段，允许同一事实由不同有效段落支持。
- 继续保留文档版本、页码和片段定位，禁止只有模型判断而没有真实来源。
- 增加 claim support、answer completeness 和 context utilization 等独立结果，不覆盖现有
  retrieval、citation 和 refusal 维度。
- LLM 或 NLI 判断只用于离线评测，并保存模型、提示词、输入摘要、结果和人工复核状态。
- 对严格拒答 case 继续使用确定性结果，不允许评测模型把内部知识回答判为通过。

这应作为独立 Issue，不能混入 #39。否则检索算法和评测口径同时变化，将无法判断数字变化来自
真实召回改善还是评分规则变化。

## 推荐实施顺序

### P0：实现 Issue #39

先解决复杂查询覆盖和确定性融合。使用现有数据集、数据库快照和 BGE-M3 重放全部 30 题，
要求当前 21 个检索通过项不退化，并让目标四题的失败数下降。

### P1：建立评测 schema v2

支持 evidence slot、等价证据和 claim-level 离线审核。旧 schema 与旧报告保持只读，不能重写
历史结果。该任务解决“合理替代证据被判错”和“端到端失败无法定位”的问题，不宣称提高产品
回答质量。

### P2：针对 ARF-011 和 ARF-012 做混合召回 A/B

固定其他变量，对比原始 dense、查询扩展后的 dense、dense 加词法或 sparse RRF。先观察目标
片段在扩大候选池中的排名，再决定是否增加 reranker。

### P3：按证据决定是否引入 reranker

只有当召回池已经包含目标片段但最终排序不稳定时才引入。优先评估 FlagEmbedding 官方提供的
多语言 BGE reranker，并单独记录模型存储、内存、延迟和 CPU 运行成本。

### P4：语料规模变化后再评估 GraphRAG

GraphRAG 不是当前六个失败 case 的低成本修复。它应由新的跨文档全局问题评测集驱动，而不是
因为项目名称流行就加入依赖。

## 结论

成熟项目确实遇到同类问题，并且形成了比较一致的解决路径：多查询或子问题分解扩大查询覆盖，
dense 与 sparse 多路召回提升 recall，RRF 融合不同排名，reranker 在高召回候选池上提升
precision，父子或相邻片段改善上下文完整性，最后用分轴和 claim-level 指标评估检索与生成。

对 SourceTrace 而言，当前 #39 的方向正确，但它只覆盖复杂查询召回。直接事实漏召回和评测
语义过严是两个独立问题，应分别通过混合召回 A/B 和评测 schema v2 解决。
