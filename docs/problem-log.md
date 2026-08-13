# SourceTrace 问题日志

本日志只记录开发和真实运行中实际出现过的问题。每项都区分症状、根因、修复和验证，不补写
不存在的性能数据。

## 1. Worker 跨事件循环复用数据库连接

**症状**：完整 Compose 上传 PDF 后，Dramatiq 重试并报连接关联到其他事件循环；文档无法
完成摄取。

**诊断**：最小复现连续两次用 `asyncio.run()` 访问同一个 SQLAlchemy async engine，第二次
稳定报事件循环已关闭。源码显示每条 Dramatiq 消息都创建新循环，而 engine 和连接池是进程
级单例。

**根因**：asyncpg 连接绑定创建它的事件循环，但旧实现把池中连接带到了下一条消息的新循环。
增加重试或把 Worker 改为单线程都不能消除连续消息之间的生命周期错误。

**修复**：注册 Dramatiq 官方 `AsyncIO` middleware，把摄取 actor 改成原生 async actor；同一
Worker 进程的消息统一提交到常驻事件循环。

**验证**：新增回归测试连续执行两条消息并断言运行循环对象相同；真实 Compose 摄取从 queued
完成到 completed。

## 2. API 容器重建后 Nginx 返回 502

**症状**：API 直接健康检查为 200，但 Web 同源 `/api/` 和 `/health` 返回 502。Nginx 日志仍
连接 API 重建前的容器 IP。

**根因**：Nginx 启动时解析一次 `api` 服务名，之后缓存旧地址；Docker 重建容器可能分配新
地址。

**修复**：使用 Docker 内置 DNS `127.0.0.11` 和短 TTL，通过变量形式的 upstream 让 Nginx
重新解析服务名，同时保留 SSE 禁用缓冲与超时配置。

**验证**：修复前形成“直连 200、代理 502”的反馈环；重建 Web 后同源代理恢复 200，后续完整
HTTP/SSE 旅程通过 Nginx 完成。

## 3. Windows 模型路径进入 Linux 容器

**症状**：Worker 已完成解析切分，但 embedding 失败。日志把 Windows 路径识别成非法
Hugging Face repository ID。

**根因**：混合开发使用的 `EMBEDDING_MODEL` 是宿主机本地路径，Compose 将它原样传入 Linux
容器。缓存挂载正确，但模型配置没有区分宿主机与容器命名空间。

**修复**：增加 Compose 专用 `EMBEDDING_MODEL_CONTAINER`。默认使用 `BAAI/bge-m3`；复用
ModelScope 完整目录时使用 `/models/huggingface/BAAI/bge-m3`。宿主机仍使用
`EMBEDDING_MODEL`。

**验证**：容器从挂载目录加载 BGE-M3，完成 embedding 并激活可检索版本，没有重新下载或把
权重复制进镜像。

## 4. Readiness 只是占位响应

**症状**：早期 `/ready` 总是返回 degraded 和 `not_configured`，无法作为 Compose 启动门禁。

**根因**：工程基线只建立了接口，尚未接入 PostgreSQL 和 Redis adapter。

**修复**：增加数据库 `SELECT 1` 与 Redis `PING` probe，通过 Service 并发编排；依赖异常时
返回 503 和 `unavailable`，`/health` 仍只表达进程存活。

**验证**：单元测试覆盖 ready 和 unavailable；完整 Compose 的 API healthcheck 实际依赖
`/ready`，数据库与 Redis 均显示 ok。

## 5. OpenAPI 生成文件未同步导致 CI 失败

**症状**：本地静态检查和测试通过，但 PR 的 `Verify generated API contract` 步骤失败。

**根因**：Readiness 响应枚举已从 `not_configured` 改为 `unavailable`，但提交前遗漏重新生成
`apps/web/openapi.json` 和 TypeScript schema。

**修复**：运行 `pnpm generate:api`，审查差异后提交两个生成文件。

**验证**：本地重复生成相对暂存内容无差异；GitHub Actions `quality` 在新提交上通过。

## 6. Nginx 默认 1 MiB 拒绝合法 PDF

**症状**：后端配置允许 20 MiB PDF，但通过 Web 同源入口上传 1.4 MiB Self-RAG 论文时返回
HTTP 413；较小文件可以上传。

**根因**：Nginx 未声明 `client_max_body_size`，使用默认 1 MiB 上限，和 API 的
`MAX_UPLOAD_BYTES=20971520` 契约不一致。

**修复**：在 server 级显式配置 `client_max_body_size 20m`，并增加配置契约测试锁定代理与
API 上限。

**验证**：测试修复前失败、修复后通过；重建 Web 后运行时 `nginx -T` 显示 20m，946 KiB
和 1.4 MiB PDF 均通过 `localhost:5173` 上传成功。

## 7. PDF 抽取的 NUL 字符无法写入 PostgreSQL

**症状**：RAG 论文完成解析和切分后，chunk 批量写入连续三次失败，公开状态只显示可重试的
临时摄取失败。

**根因**：pypdf 从论文公式中提取出 NUL 字符；PostgreSQL UTF-8 text/varchar 禁止存储
`0x00`。相同输入重试不会自行恢复。

**修复**：在 PDF parser 边界删除抽取文本中的 NUL，并将 parser provenance 升为
`pypdf-v2`。需要重新解析的人工 retry 使用当前 parser/chunk 配置；已存在 chunks 的 embedding
retry 仍复用原配置。

**验证**：最小 PDF 契约测试在修复前保留 NUL、修复后输出其余原文；真实 RAG 论文使用
`pypdf-v2` 首次摄取完成，生成 44 个 chunks。

## 8. Worker 重建后模型缓存没有持久复用

**症状**：旧 Worker 已能 embedding，但容器重建后再次访问 hf-mirror，并因网络元数据请求
失败而把文档标记为 embedding provider unavailable。挂载缓存目录只有 harness 元数据。

**根因**：加载器只在运行时设置 `HF_HOME`，相关库可能已缓存默认目录；同时 `.env` 缺少
Compose 的宿主缓存挂载和容器模型路径，容器退回远程模型 ID。

**修复**：把 `cache_folder` 显式传给 `SentenceTransformer`；本地 `.env` 将
`D:\DevelopEnvironment\huggingface` 挂载到 `/models/huggingface`，并使用已下载的
ModelScope BGE-M3 目录。模型权重不复制进镜像。

**验证**：容器内确认 2.2 GiB 权重和配置可见；日志从本地路径加载模型，无下载请求；三篇
论文共 207 个 chunks 均在 run 1、attempt 1 完成 embedding。

## 9. CPU 镜像仍包含 CUDA 运行依赖

**现状**：基础 Compose 明确以 CPU 运行且不要求 NVIDIA 硬件，但当前 Linux 锁文件解析的
PyTorch 包同时带入 CUDA 运行依赖，使 API/Worker 公共镜像构建和导出较重。

**处理**：本次没有为了缩小镜像临时改写锁文件或拆分未经验证的依赖矩阵。CPU 功能和独立
GPU override 已验证配置正确；镜像体积优化留给单独任务，通过 CPU/GPU 专用依赖组和实际
构建测量处理。

**面试表达**：这是已识别的交付成本，不应声称镜像已经轻量化，也不应在没有构建数据时填写
节省比例。

## 10. 摄取 provenance 中的容器模型路径无法在宿主机重放

**症状**：正式评测在首次检索时失败。数据集与数据库快照校验均已通过，但宿主机 CLI 尝试
从 `/models/huggingface/modelscope/BAAI/bge-m3` 加载 embedding 模型并报告路径不存在。

**根因**：摄取任务在 Compose Worker 中运行，provenance 如实记录了当时的容器模型路径；
真实评测 CLI 在 Windows 宿主机运行，却把这个部署定位符直接当作本地路径。模型 revision、
维度和配置版本可以跨环境标识同一配置，文件系统路径则不能。

**修复**：真实评测继续使用摄取 provenance 绑定报告，并校验 provider、模型标识、revision、
维度和配置版本；校验一致后，模型加载改用当前运行环境的 `settings.embedding_model`，从而
允许同一模型在容器和宿主机使用不同路径。

**验证**：回归测试覆盖“provenance 为 Linux 容器路径、Settings 为 Windows 宿主机路径”
以及 revision 不一致时拒绝重放；最小真实烟测从数据库读取三篇论文的 provenance，成功加载
本地 BGE-M3 并生成一个 1024 维查询向量。

## 11. 中转站结构化响应包含非法 JSON 转义

**症状**：正式评测已完成 embedding 和检索，并成功调用真实模型，但第一题在引用修复节点
解析 `message.content` 时失败，错误为 `Invalid \\escape`。本次失败没有生成评测报告。

**根因**：中转站返回的 OpenAI 兼容 HTTP 响应本身是合法 JSON，但模型生成的内层结构化
内容把数学文本中的 `\\(` 和 `\\)` 直接放入 JSON 字符串。它们不是 JSON 标准转义序列，
因此 Python 严格解析器拒绝该内容。

**修复**：适配器仍优先执行严格 JSON 解析。仅当解码失败时，扫描 JSON 字符串内部的
反斜杠，只把不属于 JSON 标准转义集合的反斜杠转换为字面反斜杠，然后再次严格解析。字段
集合、字段类型、完成状态和外层供应商响应契约均未放宽。

**验证**：供应商契约回归测试复现了包含 `\\(` 和 `\\)` 的引用修复响应；修复前得到
`LLM_INVALID_RESPONSE`，修复后保留原数学文本并返回引用完整的答案。完整适配器契约测试
继续通过。

## 12. MVP 验收脚本只验证事件结构，未验证关键语义

**症状**：最终规格审查发现，MVP 验收脚本只确认回答以 `final` 结束、带有一个引用且源文件
可访问，却没有确认答案和引用摘录是否包含合成 PDF 的唯一事实。它还没有断言拒答代码，
以及取消 run 不持久化答案和引用。

**根因**：初版脚本把 SSE 终态和引用结构视为足够的验收信号，遗漏了严格证据约束系统的
最小语义断言。因此模型即使对唯一事实回答错误，或拒答来自非证据不足的故障，脚本仍可能
报告成功。

**修复**：脚本现在要求最终答案和唯一引用摘录都包含 `37 days`，拒答代码必须为
`INSUFFICIENT_EVIDENCE`；取消操作必须先返回 `cancel_requested`，并在历史中确认该 run 没有
outcome、答案或引用。

**验证**：新增脚本级单元测试覆盖错误事实、错误引用摘录、错误拒答代码和取消后残留答案。
2026-08-01 通过公开 HTTP/SSE 重跑完整 Compose 旅程，所有新增断言通过，测试知识库清理返回
HTTP 204。

## 13. 目标证据进入融合候选池但未进入最终 Top 8

**症状**：提交 `45b08ac` 的 30 题真实报告中，`ARF-012`、`ARF-024` 和 `ARF-025` 的缺失
证据页已经进入最后一轮融合候选池，但最佳相关 Chunk 分别停留在基线排名 12、22 和 16，
页面多样性选择后仍无法全部进入最终主候选。

**根因**：RRF 适合合并多条查询的排名，但不直接判断“问题与候选全文是否精确相关”。候选
只在单条查询中出现或原始 cosine 排名较低时，融合分数不足以把它提升到最终 Top 8；页面
多样性只能在既有排序上避免同页挤占，不能替代语义重排。

**实验处理**：新增独立的固定候选池 reranker 评测工具。它只读取基线报告最后一轮的候选
Chunk ID，从同一数据库快照加载文本，使用 `BAAI/bge-reranker-v2-m3` 评分，再按 reranker
分数、原融合分数、最佳 cosine 和 Chunk UUID 稳定排序，并复用生产页面多样性及同页邻居
规则。工具不重新执行向量检索、不调用 LLM，也没有接入线上 `RetrievalService`。

**验证**：版本化 Dataset、基线 Report、模型 revision、权重哈希和代码提交均写入实验报告。
提交 `7a5b841` 在 RTX 5070、PyTorch `2.13.0+cu130`、批大小 8 下重排全部 30 题，retrieval
pass 从 21 增至 23；`ARF-012` 和 `ARF-025` 转为通过，原有 21 个通过项零退化，3 个不适用
样本保持不适用。`ARF-024` 仍失败，因此该结果只证明生产接入值得进入下一次设计评审，不能
宣称 reranker 已解决全部召回问题。纯重排总耗时约 27.0 秒，峰值显存约 2432 MiB；这些数字
只适用于本次固定数据、硬件和配置。

## 14. 离线有效的 reranker 尚未进入在线回答链路

**症状**：Issue #41 已证明固定候选池重排可把 retrieval pass 从 21 提升到 23，但在线
`RetrievalService` 仍在 RRF 后直接执行页面多样性选择，因此真实用户回答不会获得该改进。

**根因**：离线工具刻意与生产链路隔离，用于先验证模型收益和退化风险；在评测通过前直接把
0.6B cross-encoder 放入请求路径会提前引入模型存储、GPU、延迟和失败处理复杂度。

**修复**：生产检索现在通过窄 `Reranker` 端口，在 RRF 后把原始用户问题与完整有界候选池
交给固定 revision 的 `BAAI/bge-reranker-v2-m3`。稳定排序依次使用 reranker、RRF、最佳 cosine
和 Chunk UUID，随后才执行页面多样性与邻居扩展。adapter 负责权重 SHA-256 校验、懒加载、
进程内复用、推理串行化和线程卸载；失败会终止本次检索，不静默退回旧排序。

**可重放性**：新 Answer Run 轨迹保存 reranker provider、model、revision、配置版本、每个候选
的分数和重排排名；历史 JSON 和旧评测报告允许这些新增字段缺省，避免已有记录无法读取。

**验证**：固定候选实验结果仍为 23/30 retrieval passed、21/30 baseline passed、零退化；生产
排序、页面多样性顺序、tie-break、异常输出和单次懒加载均有回归测试。RTX 5070 使用 PyTorch
`2.13.0+cu130` 真实加载本地固定权重并完成两段文本重排；包含首次 SHA 校验和模型加载的 smoke
耗时约 12.3 秒。该时间不是稳态请求延迟，不能写成产品性能指标。全量门禁为后端 187 项、
前端 27 项通过，静态检查、类型检查与生产构建通过。

## 15. Flash 结构化响应在 JSON 后附加说明文本

**症状**：切换到 `deepseek-v4-flash` 后，真实评测在引用修复节点失败。供应商返回的首个 JSON
对象有效，但对象后还有额外说明，Python `json.loads` 报告 `Extra data`，整批评测未生成报告。

**根因**：OpenAI-compatible 适配器要求 `message.content` 的全部字符只能构成单个 JSON 文档。
既有非法反斜杠兼容路径也再次对整段调用 `json.loads`，因此两条路径都不能处理首个有效对象
之后的供应商附加文本。

**修复**：适配器从首个非空字符开始只解码第一个完整 JSON 值，再继续执行既有字典类型、字段
schema、证据白名单和引用确定性校验。首个 JSON 无效时仍执行受限反斜杠恢复；前置非 JSON 文本、
错误字段和非法引用不会因此被接受。

**验证**：新增供应商契约测试固定返回“合法引用修复 JSON + 尾随说明”，修复前稳定得到
`LLM_INVALID_RESPONSE`，修复后返回同一受引用约束的答案。完整 20 项适配器契约测试通过；随后
使用提交 `b87c635`、`deepseek-v4-flash` 和生产 BGE reranker 完成全部 30 题真实评测，不再出现
该解析故障。

## 16. 推测性多查询使既有检索结果回退

**症状**：为剩余四个检索失败题增加文档标题约束、逐查询重排和两个初始扩展查询后，真实
30 题检索从基线 23 passed、4 failed、3 not applicable 下降为 21 passed、6 failed、
3 not applicable；`ARF-015` 和 `ARF-025` 从通过变为失败，四个目标失败项均未改善。

**根因**：比较和组合题的模型查询只是检索假设，DeepSeek 生成的表达没有稳定命中各证据槽位。
额外查询的高 reranker 分数跨查询不可直接比较，却会在固定 Top 8 中挤掉原问题经页面多样性
选出的低排名有效片段。固定预留 4 或 6 个原问题候选也不能提供保证，因为基线通过项会依赖
页面多样性后的第 8 或第 9 个重排候选。

**修复**：保留标题约束、逐查询重排和完整轨迹，但把初始规划收紧为保守反例改写：只为
绝对化或否定性声明生成最多一条搜索限制或失败模式的查询；其他题保持原问题基线。第二条额外
查询额度只留给证据评估后的单次补检。结构化规划固定 `temperature=0`，策略升级为
`bounded-counterexample-v3`。

**验证**：在 `agentic-rag-foundations-v1`、同一数据库文档版本快照、本地 BGE-M3、固定
`BAAI/bge-reranker-v2-m3` 和 `deepseek-v4-flash` 上连续运行两次 30 题规划与检索，两次均为
24 passed、3 failed、3 not applicable；23 个基线通过项零回退，`ARF-030` 改善为通过。
`ARF-023`、`ARF-024` 和 `ARF-026` 仍失败，后续不能靠放宽 Top 8、阈值或证据门禁处理。

## 17. Dense 候选池无法召回 ARF-023 的精确反例证据

**症状**：`bounded-counterexample-v3` 已把 30 题检索稳定到 24 passed，但 `ARF-023` 的目标
第 11 页 chunk 在原问题和有界补充查询的 dense Top 32 中都不存在。生产 reranker 只能重排
已有候选，因此无法恢复该证据。

**离线处理**：新增只用于评测的 PostgreSQL `english` 全文检索通道。检索词和连续短语窗口
只由版本化问题与补充查询生成；lexical 与 dense 各自保持 Top 32，再以确定性 RRF 融合并复用
生产 reranker、页面多样性、最终 Top 8、最低 cosine 阈值和邻居扩展。实验不接入在线
`RetrievalService`，不创建索引，也不调用远程 LLM。

**验证**：固定目标 chunk 从 dense 未命中变为 lexical 第 13、融合第 26、reranker 第 4并进入
主证据。全量检索从 24 passed、3 failed、3 not applicable 变为 25 passed、2 failed、
3 not applicable；只改善 `ARF-023`，无通过项退化。两次完整报告 SHA-256 均为
`7ac146d150b9d9413aae577f74471a0b3dbe221044fbc31abd8bca1cc373885c`。这支持另开生产接入
Issue，但不能表述为线上回答准确率提升；`ARF-024` 和 `ARF-026` 仍需文档范围的多证据规划。

## 18. 离线 lexical-hybrid 收益尚未进入生产检索

**症状**：Issue #50 已证明 PostgreSQL lexical 通道能召回 `ARF-023` 的精确反例证据，但该
实现只存在于评测代码。在线 `RetrievalService` 仍只调用 dense 搜索，生产数据库也没有全文
检索索引，因此真实用户请求无法获得该收益。

**根因**：离线实验刻意隔离了生产 repository、迁移和公开 Answer Run 轨迹，用于先验证收益
与退化风险。实验查询 SQL 与生产 dense 查询分离，继续复制会造成两个检索实现漂移。

**修复**：生产 `PgVectorRetrievalRepository.search` 现在封装 dense、按查询条件启用的
PostgreSQL `english` lexical 通道和通道级 RRF；`RetrievalService` 只依赖这一查询级接口。
评测也直接复用该 repository，dense 基线则显式调用 `search_dense`。新增可回滚的并发 GIN
迁移；Answer Run 轨迹与公开历史响应记录各通道排名、分数和融合排名。历史记录允许新增字段
缺省。

**验证**：真实 PostgreSQL 完成迁移 upgrade、downgrade 和恢复，`EXPLAIN` 可选择 GIN 索引。
两次完整 30 题生产 repository 重放均为基线 24 passed、混合检索 25 passed、3 not
applicable，只改善 `ARF-023`，无退化；两份报告 SHA-256 同为
`1439add27519fe48dec2aee3aaa589c72bf08dfdbed533d0b01ad09a47397822`。该结果未调用 DeepSeek，
不代表端到端回答准确率；`ARF-024` 和 `ARF-026` 仍未解决。

## 19. 单阶段证据槽位查询缺少论文特有机制词

**症状**：Issue #52 的生产混合检索已有 25 个通过项，但 `ARF-024` 和 `ARF-026` 需要多个
文档或组件的证据。手工证据槽查询可使两题通过，真实 DeepSeek 单阶段规划却只生成“生成前
检索”和“生成中按需检索”等宽泛复述，完整 30 题仍为 25 个 hybrid 通过项。

**根因**：单次结构化响应同时承担槽位识别、论文归属和源术语改写。Flash 模型能识别 RAG 与
Self-RAG 两个槽位，但在同一上下文中不会稳定补出 `top-k documents`、`[RETRIEVE] token`
等论文特有检索对象。仅把每个查询限定到对应文档仍未选中 Self-RAG 的目标页，说明标题路由
不能代替查询细化。继续加入题目专用示例会造成评测过拟合。

**修复**：`two-stage-evidence-slots-v5` 先用结构化 `evidence_groups` 识别最多三个有序槽位，
再按总预算选择最多两个附加槽。多槽计划随后对每个选中槽独立并发细化；每次只看到原规划输入
和自身第一阶段槽位，不读取其他槽位结果、PDF、检索候选或评测答案。细化必须产生不同查询并
保持原文档标题；改标题、原样返回、损坏结构或供应商错误都会丢弃该槽。简单事实和单槽反例
计划不增加调用，`RetrievalService` 继续强制整个 Answer Run 最多两条附加查询。

**验证**：真实 DeepSeek v5 为 `ARF-024` 生成包含 `top-k documents` 与 `[RETRIEVE] token`
的两条查询，聚焦检索由失败变为通过。固定 v5 查询计划的两次完整 30 题生产 repository 重放
均为 baseline 25 passed、hybrid 26 passed、3 not applicable、零回归；报告 SHA-256 均为
`69036a6750fb533b86664bbd7f5871a9a41e3d0a5e3d887d672a55b69066c424`。`ARF-026` 因第一阶段
重复文档归属而安全回退，仍是后续独立问题；本结果不代表端到端回答准确率。

## 20. 证据评估响应偶发包含多余顶层字段

**症状**：`deepseek-v4-flash` 的完整真实评测在证据评估阶段返回合法 JSON 对象，但顶层字段集合
除 `sufficient`、`selected_chunk_ids` 和 `supplemental_queries` 外偶发包含说明字段。适配器按
严格契约拒绝该响应，整轮评测没有生成报告。

**根因**：结构化调用只对网络错误、协议错误和空内容执行重试；证据评估器在解析 JSON 后才检查
业务 schema，因此字段集合错误没有一次受控纠错机会。直接忽略多余字段会掩盖供应商契约漂移，
并可能让未经验证的内容进入后续回答链路。

**修复**：只在首个响应的顶层字段集合不正确时追加严格 system 指令并重试一次。第二次仍不正确
则返回 `LLM_INVALID_RESPONSE`；字段集合正确但类型错误、值为空、存在重复项或超过补充查询预算
时不重试并继续拒绝。纠错提示不回传供应商的无效响应，也不改变证据、引用或检索门禁。

**验证**：新增契约测试先稳定复现“首答多一个 `explanation` 字段”导致的失败，再验证第二次严格
响应可恢复；另以调用次数断言连续字段错误恰好调用两次、查询超限只调用一次。`pnpm check`、
219 项后端测试、27 项 Web 测试和生产构建通过。提交 `03a2306` 随后完成 30 题真实评测并生成
通过项目报告模型校验的报告，未再因该供应商格式偏差中止。

## 21. 补充查询提前猜测待求证的论文归属

**症状**：`ARF-026` 已在数据集中登记经人工批准的 ReAct 第 3 页替代证据，但完整真实评测的
初始规划因重复文档标题安全回退后，证据评估器生成 `environment action in RAG paper`；后续
探针还出现 `环境动作 critique token Self-RAG`。这些查询把用户正在询问的归属关系当作已知
事实，检索不到 ReAct 环境动作片段，严格策略因此拒答。

**根因**：证据评估提示只要求每条补充查询针对一个缺失组件，没有禁止加入候选证据尚未建立的
所有者，也没有明确禁止把已支持的另一个术语混入同一查询。初始规划器的标题只是检索假设，
证据评估器复制该假设后形成循环：必须先知道术语属于哪篇论文，才能检索证明其属于哪篇论文。

**修复**：`evidence-assessment-v3` 要求每条补充查询只包含一个缺失组件，多个缺口在剩余预算内
分别查询；不得加入问题或候选证据尚未建立的论文、方法、框架、组件所有者或关系；已由所选
候选支持的其他术语不能混入。初始规划存在重复文档归属且一次纠错仍失败时继续整体回退，不从
语义冲突响应中保留看似结构合法的槽位。查询总预算、Top K、候选池、阈值和门禁不变。

**验证**：契约测试在修复前稳定返回错误的 `environment action in RAG paper`，修复后返回单一
缺失组件查询。真实证据判断探针把原先混合查询拆为独立的 critique token 与环境动作查询；
ARF-026 聚焦及完整 30 题运行均使 ReAct 声明命中 `approved_alternative`，检索状态由 failed
变为 passed。全量检索为 25 passed、2 failed、3 not applicable，上一轮 24 个检索通过项零
退化。引用维度仍存在独立波动，不属于本问题的完成声明。

## 22. 证据充分但自由文本引用修复仍不稳定

**症状**：固定真实评测中，一批 Answer Run 已通过 Retrieval 和最终 Evidence Decision，但初稿
与唯一一次 Citation Repair 都以 `uncited_claim` 被确定性校验拒绝。旧轨迹只记录一个失败类别，
无法判断是全部结构单元、开头单元还是中间单元缺少引用，也无法比较初稿与修复稿是否以同一
方式失败。

**根因**：提示词要求模型直接生成或重写带 UUID 引用的自由文本，供应商既要保持声明语义，又要
精确复制标签并放到每个事实单元中。模型有时原样返回草稿、只给部分句子加引用，或返回正文中
已有 UUID 与结构字段不一致的结果。继续增加自然语言强调无法把格式正确性变成稳定契约。

**修复**：决策轨迹现在按 `initial` 和 `repair` 阶段记录结构单元总数、引用数、未引用单元索引
和未知标签单元索引，不保存被拒绝正文。Citation Repair 改为结构化 `claims`：每项只接受
`text` 与允许的 `citation_ids`，服务端移除允许的行内重复标签后确定性渲染引用。未知 UUID、
空 claim、空引用、错误字段集合和重复证据选择继续拒绝；只有第一次空引用允许一次严格纠错，
渲染结果仍必须重新通过原有确定性门禁。

**验证**：单元测试通过公开 `AnswerWorkflow.run` 接缝稳定复现“证据充分 -> 初稿无引用 -> 一次
修复仍无引用 -> Refusal”，并区分两次校验的失败单元；供应商契约覆盖结构声明、重复允许标签、
未知 UUID、空引用纠错和非法 Schema。该修复没有降低 Knowledge Base、Evidence Decision、
Citation 或 Refusal 门禁。可单独运行以下回归命令：

```powershell
uv run --project apps/api --extra cpu pytest `
  apps/api/tests/unit/test_answer_workflow.py::test_workflow_refuses_when_the_single_citation_repair_is_still_invalid -q
```

新增阶段化诊断断言在修复前失败，修复后命令通过；测试不调用网络或数据库。行为稳定化前的
`c1aefe4` 真实报告提供了三类代表性持续失败，以下分类只使用结构计数和索引，不保留回答正文：

- `ARF-003`：初稿与修复稿都是 5 个单元、0 个引用，未引用索引始终为 0 至 4。假设是自由文本
  修复没有执行引用任务而是近似原样返回；若结构化 claims 能生成非空允许引用并通过确定性
  校验，则该假设得到支持，若仍为全零引用则被证伪。
- `ARF-015`：两次都是 8 个单元、7 个引用，仅索引 0 持续未引用。假设是开头结构单元容易被
  模型视为标题或引言而跳过；若服务端按 claim 的每个确定性单元渲染后索引 0 仍缺失，则该
  假设被证伪。
- `ARF-021`：两次都是 7 个单元，引用数从 8 增至 9，但索引 3 持续未引用。该 case 证伪了
  “问题只发生在首单元”的单一解释，并支持自由文本修复可能遗漏任意中间单元；若结构化渲染
  后仍只遗漏索引 3，则应转向单元切分或 claim 覆盖假设。

随后在提交 `fb5ea7c7e14747c27f1678475eaa0d74b0ee40d8` 上使用
`agentic-rag-foundations@1.1.0`、`deepseek-v4-flash`、生产混合检索和固定 BGE reranker
完成新的 30 题真实回归。原始报告 SHA-256 为
`d62a2cd90b2892a8164c2ad4d85be768edbad1738eafaa00a211502291d3a34b`。旧报告中 9 个
“最终证据充分、初稿与修复稿仍因 `uncited_claim` 拒答”的 answerable case，在新报告中降为
0；新报告没有任何 answerable case 因引用校验失败而拒答，验证结构化声明与服务端确定性渲染
解决了本问题描述的缺陷。

用户逐条审核全部 13 个待审回答，其中 12 个通过；`ARF-011` 内容正确但未跟随中文问题的语言，
判定失败。绑定原始报告的 judgment 位于
`evals/judgments/agentic-rag-foundations-v1-fb5ea7c-deepseek-v4-flash.json`。审核后端到端结果为
15 passed、15 failed、0 pending review。该结果不能泛化为产品准确率；14 个自动引用失败表示
版本化期望证据覆盖或预期拒答不满足，不等同于本条问题的引用格式缺陷。4 个 answerable refusal
发生在证据充分性阶段，也应与引用修复问题分开处理。

## 23. DeepSeek 兼容响应的终态、重试和 thinking 语义不稳定

**症状**：真实 DeepSeek 调用先后出现非 `stop` 终态、结构化空正文、额外字段、网络断连和供应商
错误。旧适配器把多种原因合并为格式错误或供应商不可用，难以判断是否可恢复；DeepSeek V4 又
默认开启 thinking，可能改变结构化 JSON 与最终流式回答的响应形态和 token 成本。

**根因**：OpenAI-compatible 只描述传输外形，不保证不同平台具有相同的终态、错误码、JSON
Output、thinking 和 SSE 细节。适配器此前没有把 DeepSeek 官方五类 `finish_reason`、HTTP 状态、
流式输出前后断连、keep-alive、usage-only chunk 和 `[DONE]` 完整性建模为明确契约。

**修复**：结构化调用默认显式发送 `response_format.type=json_object`、
`thinking.type=disabled` 和有限 `max_tokens`，四类结构提示词包含与业务 Schema 对齐的 JSON
示例；结果必须依次通过 `stop`、非空、JSON 和精确 Schema。最终流式回答使用独立 thinking
配置，DeepSeek 基线默认关闭，不支持该扩展的平台可设为 `default` 以省略字段。适配器分类五类
官方终态与 400、401、402、422、429、500、503；瞬态 HTTP、网络、协议和资源不足只在尚未输出
正文时有界重试一次。流式解析忽略 keep-alive、reasoning content 和 usage-only chunk，要求
明确 `stop`，拒绝终态后正文和 `[DONE]` 前断连。

**验证**：供应商契约 fake 覆盖正常结构化和流式响应、空正文、Schema 偏差、五类终态、各类
HTTP 状态、输出前后断连、keep-alive、usage-only chunk、reasoning content、thinking 省略回退
和安全错误信息；完整静态检查、后端与前端测试和生产构建通过。当前仍使用覆盖整个调用生命周期
的单一应用 deadline；connect、等待首 token 和 read timeout 的拆分属于后续独立问题。

## 24. 引用评分失败缺少可复用的去敏诊断

**症状**：`fb5ea7c` 真实报告有 10 个实际已回答但引用评分失败的 case。原报告包含完整问题、回答、
候选和论文片段，手工解析既难以重放，也不适合提交或直接用于 Issue 诊断；现有
`diagnose-retrieval` 只覆盖检索失败，无法判断目标证据是否已召回但未被最终 Citation 使用。

**修复**：新增纯离线 `diagnose-citations` 命令和 `citation-diagnostics-v1` Schema。工具复用统一
声明级证据匹配，只输出 case/claim ID、文档版本 ID、页码、检索/引用匹配状态、失败机制、源
Report SHA-256 和原运行配置。问题、参考答案、模型回答、提示词及文档正文均不进入诊断产物；
命令不批准替代证据，也不修改 Dataset、Report 或线上门禁。

**验证**：使用 `agentic-rag-foundations@1.1.0` 和 reviewed report SHA-256
`4d0b9361951ca1c5bbcf5606d43e32d62831a6b70f23800179bff059636ea0b5` 离线生成两次诊断；两份
产物字节相同，SHA-256 均为
`8709094a18d3bf7375321307c57faf38dbce8e830f8de508b234169819f0c9f0`。诊断得到 10 个 failed
answered case：

- `ARF-001`、`ARF-004`、`ARF-006`、`ARF-008`、`ARF-012`、`ARF-013`、`ARF-015`、
  `ARF-018` 共 8 个为 `retrieved_but_not_cited`，即所有目标声明的规范证据已召回，但最终引用
  没有命中规范证据或已批准替代证据。
- `ARF-025` 为 `partial_claim_coverage`：两个目标声明均召回规范证据，但最终只引用第一个声明
  的规范证据。
- `ARF-024` 为 `expected_evidence_not_retrieved`：第一个声明召回规范证据，第二个声明未召回，
  两个声明的最终引用都未命中目标证据。

该分类证伪了“10 个失败都来自 embedding”的单一解释，也不能证明实际引用都是可批准的等价
证据。后续应分开处理：先人工核验 8 个完全偏移和 1 个部分覆盖 case 的实际引用是否语义等价；
只有通过核验的片段才能作为声明级 `approved_alternatives`。`ARF-024` 的缺失声明继续进入独立
检索诊断。若实际引用不等价，再单独修改 Evidence Decision 或声明覆盖策略，并重新运行真实
评测，不能在本诊断 Issue 中直接放宽匹配规则。

## 25. 等价引用未进入声明级评测真值

**症状**：Issue #61 的离线诊断发现 9 个 answerable case 已引用同一论文中的其他片段，但
`agentic-rag-foundations@1.1.0` 只接受最初选定的规范片段。仅凭词面不匹配无法判断实际引用是
等价证据、较弱证据还是错误声明；自动把模型引用写入 Dataset 会污染评测真值。

**审核**：用户逐项对照问题、实际回答、规范页/片段和实际引用页/片段。审核绑定 reviewed report
SHA-256 `4d0b9361951ca1c5bbcf5606d43e32d62831a6b70f23800179bff059636ea0b5`，结论如下：

- `ARF-001`、`ARF-004`、`ARF-008`、`ARF-012`、`ARF-013`、`ARF-015` 和 `ARF-025`
  的对应实际引用获批为声明级等价证据。
- `ARF-018` 的证据获批；回答语言与中文问题不一致仍是独立失败，不能由证据替代掩盖。
- `ARF-006` 的引用不获批：实际回答把 TriviaQA 扩展为“领先结果”，原文只支持在该数据集上
  强于特定预训练方法。该 case 保持原评分真值，并转入回答范围控制问题。

**修复**：Dataset 升级为 `agentic-rag-foundations@1.2.0`。只把 8 个获批 case 的最小连续原文
加入对应 `claim_id` 的 `approved_alternatives`；多声明 case 为每个证据补充稳定 claim ID。
`ARF-006` 不增加替代项。绑定查询计划同步到 1.2.0，但检索查询、模型配置和线上 Citation、
Evidence Decision、Refusal 门禁均不变。

**验证边界**：本次只更新人工审核后的评测真值，不调用真实供应商，也不把 1.1.0 历史报告的
15/30 结果重写成 1.2.0 分数。离线证据匹配用于确认获批片段确实存在于该 reviewed report 的
实际引用中；新版端到端结果必须由后续绑定 1.2.0 的独立真实评测产生。
