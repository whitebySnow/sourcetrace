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

## 6. CPU 镜像仍包含 CUDA 运行依赖

**现状**：基础 Compose 明确以 CPU 运行且不要求 NVIDIA 硬件，但当前 Linux 锁文件解析的
PyTorch 包同时带入 CUDA 运行依赖，使 API/Worker 公共镜像构建和导出较重。

**处理**：本次没有为了缩小镜像临时改写锁文件或拆分未经验证的依赖矩阵。CPU 功能和独立
GPU override 已验证配置正确；镜像体积优化留给单独任务，通过 CPU/GPU 专用依赖组和实际
构建测量处理。

**面试表达**：这是已识别的交付成本，不应声称镜像已经轻量化，也不应在没有构建数据时填写
节省比例。
