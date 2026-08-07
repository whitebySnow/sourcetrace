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
