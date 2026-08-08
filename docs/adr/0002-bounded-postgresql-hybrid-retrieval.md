# 0002: 采用有界 PostgreSQL 混合召回

状态：accepted
日期：2026-08-08

## 背景

ADR 0001 将 BM25、稀疏索引和其他召回通道留给独立 A/B，避免在缺少证据时同时扩大候选范围
并改变排序语义。Issue #50 随后使用同一 30 题人工审核数据集、不可变文档版本快照、本地
BGE-M3 和固定 BGE reranker 对 PostgreSQL 英文全文检索进行了两次确定性离线实验。

Dense 基线两次均为 24 passed、3 failed、3 not applicable；有界 lexical-hybrid 路径为
25 passed、2 failed、3 not applicable。唯一改善为 `ARF-023`，原有 24 个通过项零退化。
目标 Chunk 在 dense Top 32 中缺失，但进入 lexical 第 13、通道融合第 26并由 reranker 提升
到第 4。两份完整报告的 SHA-256 相同。

## 决定

生产 retrieval repository 采用一个查询级深模块接口。调用者提供 Knowledge Base ID、原始
Retrieval Query、对应 dense embedding 和候选上限；PostgreSQL adapter 在内部执行以下流程：

1. 在 Active Searchable Version 范围内读取有界 dense cosine 候选。
2. 当查询至少包含四个可用拉丁或技术词项时，使用 PostgreSQL `english` 全文检索读取同样
   有界的 lexical 候选；否则保持 dense-only。
3. 每个 Chunk 在每个通道至多贡献一次 RRF 分数，按通道 RRF、最佳 cosine 和 Chunk UUID
   稳定排序，并只在融合后截断到原有候选上限。
4. 返回逐通道排名与分数；`RetrievalService` 继续执行既有逐查询 reranker、查询覆盖、页面
   多样性、最终 Top 8和同页邻居扩展。

英文全文检索表达使用查询派生词项和连续二至四词短语，不读取答案或预期证据。Chunk 文本采用
与查询完全一致的 PostgreSQL `english` `to_tsvector` 表达式，并通过 GIN expression index
加速。索引由可回滚 Alembic 迁移管理。

本决策只替代 ADR 0001 中“混合召回仍待 A/B 决定”和规格 6.1 中“首版不加入 BM25 或稀疏
检索”的部分。ADR 0001 的查询预算、逐查询重排、覆盖规则、最终 Top 8和门禁决策继续有效。

## 替代方案

1. 继续仅使用 dense：无法把不存在于 dense 候选池的 `ARF-023` 目标交给 reranker。
2. 直接采用 BGE-M3 sparse：需要新的稀疏向量存储、索引和评分契约；现有 PostgreSQL 实验已
   达到目标，因此当前没有引入该复杂度的证据。
3. 在 `RetrievalService` 暴露 `search_dense` 与 `search_lexical`：会把通道编排、SQL 评分和
   截断规则泄漏给调用者，降低模块深度并扩大测试表面。
4. 不建 GIN 索引并在请求时扫描 Chunk：小数据集可运行，但生产开销随知识库增长线性增加，
   不符合在线检索的性能契约。

## 后果

- repository 搜索接口增加原始查询参数，返回候选的 dense、lexical 和通道融合诊断值；
  `RetrievalService.search` 与 `AnswerWorkflow.run` 接口保持不变。
- Answer Run 轨迹和检索配置版本升级，以区分 dense-only 历史运行和 hybrid 新运行。
- 英文或包含足够技术词项的查询获得 lexical 通道；其他查询继续依赖 BGE-M3 dense，不把英文
  analyzer 的无效结果混入候选。
- 摄取写入 Chunk 时无需维护新的权威数据列，但迁移会为既有 Chunk 创建 GIN expression index。
- `ARF-024` 与 `ARF-026` 不属于本决策范围，继续由独立的文档范围多证据规划任务处理。
