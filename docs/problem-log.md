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
ModelScope 完整目录时使用 `/models/huggingface/modelscope/BAAI/bge-m3`。宿主机仍使用
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
