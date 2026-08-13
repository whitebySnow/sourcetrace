# Agentic RAG Foundations 1.2.0 Citation Alternative Review

- Reviewer: `whitebySnow`
- Reviewed at: `2026-08-13T04:21:31Z`
- Source reviewed report SHA-256:
  `4d0b9361951ca1c5bbcf5606d43e32d62831a6b70f23800179bff059636ea0b5`
- Resulting Dataset: `agentic-rag-foundations@1.2.0`
- GitHub Issue: `#62`

This record preserves the user's explicit decisions. Excerpts are the minimum continuous text needed
to audit the decision; the untracked report in `output/evals/` remains the authority for the complete
provider response. Approval means only that the cited passage can satisfy the named evidence claim.
It does not override answer-language or unsupported-claim failures.

## ARF-001

**Question**: 原始 RAG 论文把哪两类记忆结合起来？

**Actual answer**: 参数化记忆与非参数化记忆；前者是预训练 seq2seq，后者是通过神经检索器访问
的 Wikipedia 稠密向量索引。

- Claim: `rag-memory-types`
- Canonical: RAG `c9b7c41e-4716-403f-a115-3cdaa3a86741`, p1,
  `parametric and non-parametric memory for language generation`
- Actual citation: RAG p2, `parametric memory is a pre-trained seq2seq transformer, and the
  non-parametric memory is a dense vector index of Wikipedia`
- Decision: **approved equivalent**. The cited passage directly and more fully defines the same two
  memory types.

## ARF-004

**Question**: RAG 如何从文档索引中找到 top-K 文档？

**Actual answer**: 使用 MIPS；实验使用 FAISS 构建 MIPS 索引，并以 HNSW 近似实现快速检索。

- Claim: `rag-dense-mips-retrieval`
- Canonical: RAG p3, `Product Search (MIPS) problem`
- Actual citations: RAG p2, `use Maximum Inner Product Search (MIPS) to find the top-K documents`;
  RAG p4, `document encoder to compute an embedding for each document, and build a single MIPS
  index`
- Decision: **approved equivalent**. The Dataset stores the p4 passage because it supports both the
  dense representation and MIPS portions of the claim.

## ARF-006

**Question**: RAG 论文在哪些开放域问答数据集上报告了领先结果？

**Actual answer**: Natural Questions、TriviaQA、WebQuestions 和 CuratedTrec 四个数据集都报告了
领先结果。

- Canonical: RAG p2, `achieve state-of-the-art results on open Natural Questions, WebQuestions and
  CuratedTrec`
- Actual citation: RAG p2, `achieve state-of-the-art results on open Natural Questions,
  WebQuestions and CuratedTrec and strongly outperform recent approaches ... on TriviaQA`
- Decision: **rejected**. The source distinguishes three state-of-the-art results from outperforming
  a specific class of approaches on TriviaQA. No alternative was added; the generation defect is
  routed to Issue `#63`.

## ARF-008

**Question**: Jeopardy 生成任务的人类评估如何比较 RAG 与 BART 的事实性？

**Actual answer**: BART better 7.1%，RAG better 42.7%，both good 11.7%，both poor 17.7%，
no majority 20.8%。

- Claim: `rag-jeopardy-factuality`
- Canonical: RAG p6, `RAG was more factual in 42.7% of cases`
- Actual citation: RAG p8 Table 4, `BART better 7.1% ... RAG better 42.7% ... Both good 11.7%
  ... Both poor 17.7% ... No majority 20.8%`
- Decision: **approved equivalent**. The table is the direct, more complete source of the comparison.

## ARF-012

**Question**: ReAct 在论文中用于哪两个知识型任务？

**Actual answer**: 多跳问答 HotPotQA 和事实验证 FEVER。

- Claim: `react-knowledge-benchmarks`
- Canonical: ReAct `eb8f384b-d03b-42da-b813-4dedc1c39760`, p1,
  `answering (HotpotQA) and fact verification (Fever)`
- Actual citation: ReAct p3, `question answering (HotPotQA ...), fact verification (Fever ...)`
- Decision: **approved equivalent**. The cited passage directly names both tasks.

## ARF-013

**Question**: ReAct 在论文中用于哪两个交互式决策基准？

**Actual answer**: ALFWorld 和 WebShop。

- Claim: `react-alfworld-benchmark`; canonical: ReAct p1, `interactive decision making benchmarks
  (ALFWorld and`; actual citation: ReAct p3, `text-based game (ALFWorld, Shridhar et al., 2020b)`;
  decision: **approved equivalent**.
- Claim: `react-webshop-benchmark`; canonical: ReAct p1, `WebShop), ReAct outperforms imitation
  and reinforcement learning methods`; actual citation: ReAct p3, `webpage navigation (WebShop,
  Yao et al., 2022)`; decision: **approved equivalent**.

## ARF-015

**Question**: ReAct 与只执行动作的 Act 基线有什么区别？

**Actual answer**: ReAct 增加 thought/reasoning trace 来支持决策，Act 只有动作；回答随后列出
ALFWorld、WebShop 和知识型任务上的性能差异。

- Claim: `react-act-reasoning-difference`
- Canonical: ReAct p7, `same trajectories, but without thoughts`
- Actual citations: ReAct p3, `a thought or a reasoning trace ... aims to compose useful information
  by reasoning over the current context ... to support future reasoning or acting`; ReAct p8,
  `without any thoughts at all, Act fails to correctly decompose goals ... or loses track of the
  current state`
- Decision: **approved equivalent**. The Dataset stores the p3 direct definition; p8 corroborates the
  effect of omitting reasoning.

## ARF-018

**Question**: Self-RAG 的 reflection tokens 用来做什么？

**Actual answer**: The English response says reflection tokens decide when to retrieve and evaluate
relevance, support, and usefulness; it lists Retrieve, ISREL, ISSUP, and ISUSE.

- Claim: `self-rag-retrieval-control`; canonical: Self-RAG
  `41f20261-5c9e-4856-a5ff-9dc37da4203d`, p3, `signal the need for retrieval`; actual citation:
  Self-RAG p4, `Retrieve ... Decides when to retrieve with R`; decision: **approved equivalent**.
- Claim: `self-rag-output-evaluation`; canonical: Self-RAG p3, `relevance, support, or completeness`;
  actual citation: Self-RAG p4, `Rank yt based on ISREL, ISSUP, ISUSE` with the table definitions;
  decision: **approved equivalent**.
- Separate decision: **answer-quality failure remains** because a Chinese question received an
  English answer. Evidence approval cannot override that failure; it is routed with ARF-011 to
  Issue `#64`.

## ARF-025

**Question**: ReAct 与 Self-RAG 分别如何利用模型生成的中间信号？

**Actual answer**: ReAct 交错生成推理轨迹和任务动作；Self-RAG 使用 retrieval 和 critique
reflection tokens 决定检索并评价相关性、支持度和效用。

- Claim: `react-intermediate-signals`; canonical and actual citation: ReAct p1, `generate both
  reasoning traces and task-specific actions in an interleaved manner`; decision: **canonical
  retained**, no alternative needed.
- Claim: `self-rag-intermediate-signals`; canonical: Self-RAG p3, `signal the need for retrieval`;
  actual citation: Self-RAG p1, `Reflection tokens are categorized into retrieval and critique tokens
  to indicate the need for retrieval and its generation quality respectively`; decision:
  **approved equivalent**.

## Boundary

Only the approved passages above were added to the matching claim IDs. ARF-006 remains rejected.
This review does not rewrite the historical 1.1.0 report or establish a 1.2.0 end-to-end score. A new
score requires a separately authorized real-provider run bound to Dataset 1.2.0.
