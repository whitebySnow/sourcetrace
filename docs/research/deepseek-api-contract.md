# DeepSeek API 契约与 SourceTrace 适配建议

## 调研范围

本文核对 DeepSeek 官方 OpenAI 兼容 Chat Completions API，重点覆盖 SourceTrace 使用的
非流式结构化输出、流式回答、终止原因、思考模式、错误与连接生命周期。

本文是一份工程调研记录，不替代 `docs/specification.md`、`docs/architecture.md` 或 ADR。
文中的“官方契约”来自 DeepSeek 一手资料；“SourceTrace 建议”是根据这些契约作出的项目
设计判断，不代表 DeepSeek 官方规定。

所有资料访问日期均为 **2026-08-12**。

## 结论摘要

1. 当前官方 Chat Completions 模型名为 `deepseek-v4-flash` 和 `deepseek-v4-pro`；旧名称
   `deepseek-chat` 与 `deepseek-reasoner` 已退役。
2. `finish_reason` 的合法终态有五种：`stop`、`length`、`content_filter`、`tool_calls`、
   `insufficient_system_resource`。它们代表不同语义，不能统一映射为格式错误。
3. SourceTrace 的结构化结果只有同时满足 HTTP 成功、`finish_reason=stop`、`content` 非空、
   JSON 解码成功和业务 Schema 校验成功时才能接受。
4. DeepSeek 官方明确说明 JSON Output 偶尔会返回空内容；即使终止原因为 `stop`，也仍需
   验证内容和 Schema。
5. V4 默认开启 thinking。结构化证据判断和引用修复应显式关闭 thinking；流式最终回答若
   开启 thinking，必须区分 `reasoning_content` 与 `content`，前者不能作为答案正文。
6. 流式解析必须容忍 SSE keep-alive 注释、中间 chunk 的空终止原因、可选 usage chunk，
   并把 `[DONE]` 前断连视为未完整完成。
7. 官方只明确建议短暂等待后重试 500 和 503，并要求 429 降速；具体退避、次数和客户端
   超时秒数均未由官方规定，必须作为 SourceTrace 的有界工程策略记录。

## 当前官方端点与模型

DeepSeek 的 OpenAI 格式 base URL 是 `https://api.deepseek.com`，对话接口是
`POST /chat/completions`。官方 API Reference 当前只列出 `deepseek-v4-flash` 和
`deepseek-v4-pro`。模型页注明二者支持 thinking 与 non-thinking、JSON Output 和 Tool Calls，
上下文长度为 1M，最大输出为 384K；Flash 当前对应 DeepSeek-V4-Flash-0731。

旧模型名 `deepseek-chat` 与 `deepseek-reasoner` 的弃用截止时间是 2026-07-24 15:59 UTC，
不应继续作为新的稳定配置。

来源：

- [Your First API Call](https://api-docs.deepseek.com/quick_start/pricing/)，访问日期：2026-08-12。
- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：2026-08-12。
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，访问日期：2026-08-12。
- [Change Log](https://api-docs.deepseek.com/updates/)，访问日期：2026-08-12。

## 非流式结构化输出

### 官方契约

非流式成功响应是 HTTP 200 JSON。`choices[].finish_reason` 是必填字段；
`choices[].message.content` 可为空，thinking 模式的 `reasoning_content` 与 `content` 同级。

JSON Output 的启用条件和注意事项是：

1. 请求设置 `response_format={"type":"json_object"}`。
2. system 或 user 提示词明确包含 `json`，并提供期望 JSON 格式示例。
3. 合理设置 `max_tokens`，防止 JSON 中途截断。
4. 如果只设置 `response_format` 而没有在提示词要求 JSON，模型可能持续输出空白直到 token
   上限，看起来像请求卡住。
5. 官方明确承认 JSON Output 偶尔会返回空 `content`，并建议调整提示词缓解。

`json_object` 保证的是模型输出有效 JSON 字符串，不等于结果满足 SourceTrace 的字段、类型、
引用 ID 和业务语义。因此供应商格式保证不能替代本地 Schema 和业务校验。

来源：

- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)，访问日期：2026-08-12。
- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：2026-08-12。

### SourceTrace 接受条件

结构化规划、证据判断和引用修复应使用同一接受门禁：

1. HTTP 请求成功。
2. 响应恰有预期的 choice。
3. `finish_reason` 为 `stop`。
4. `message.content` 是非空字符串。
5. 内容可解码为 JSON。
6. JSON 满足该用例的精确 Schema 和业务不变量。

任一条件不满足都不能把部分输出当作事实。空内容或 Schema 违规可以附加更明确的 JSON
Schema 或示例后纠错一次；第二次仍失败即终止。该“一次”是 SourceTrace 的成本与确定性策略，
不是 DeepSeek 官方规定。

## `finish_reason` 分类

官方定义了以下五个终态。中间流式 chunk 的该字段可以为 `null`，不代表失败。

| 终止原因 | 官方含义 | SourceTrace 建议 |
| --- | --- | --- |
| `stop` | 自然停止或命中请求提供的 stop sequence | 继续执行非空、JSON、Schema 或引用校验；不能仅凭 `stop` 接受 |
| `length` | 达到 `max_tokens`，或请求超过模型上下文长度 | 丢弃截断内容；先区分输出预算和上下文预算，再决定是否调整后重试一次 |
| `content_filter` | 内容因安全过滤而省略 | 丢弃部分内容，分类为不可自动恢复，不原样重试 |
| `tool_calls` | 模型选择调用工具 | 是独立协议分支；仅在用例声明工具时处理，否则作为意外协议终态拒绝 |
| `insufficient_system_resource` | 推理系统资源不足导致请求中断 | 丢弃部分内容，作为供应商瞬态故障退避后重试一次 |

来源：[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：
2026-08-12。

### 本次失败的判断

本次真实评测只能确认供应商返回了非 `stop` 的 `finish_reason`。当前适配器没有保留该字段的
安全分类，因此无法根据现有日志判断它究竟是 `length`、`content_filter`、`tool_calls`、
`insufficient_system_resource` 还是一个未知值；尤其不能直接断言本次是系统资源不足。

后续适配器不应再只报告模糊的“`finish_reason != stop`”，而应至少记录固定、安全的枚举原因，
且不记录响应正文：

- `provider_finish_length`
- `provider_finish_content_filter`
- `provider_finish_tool_calls`
- `provider_finish_insufficient_system_resource`
- `provider_finish_unknown`

具体处理应为：

- `insufficient_system_resource`：在应用总 deadline 内退避并最多重试一次；再次发生则安全失败。
- `length`：若本地 token 预算证明输入仍有空间，可提高受限的输出预算或缩减提示后重试一次；
  如果上下文已满则直接失败，禁止原样无限重试。
- `content_filter`：不使用部分输出，不重复发送相同内容。
- `tool_calls`：SourceTrace 当前无工具的结构任务不应触发此分支；若出现则按契约异常失败。
- 未知字符串：为前向兼容保留安全分类，但不自动重试。

退避算法、最大一次重试和安全原因名称均是 SourceTrace 工程建议。DeepSeek 官方只定义了
终止语义，没有规定此处的重试次数。

## Thinking 与 `reasoning_content`

DeepSeek V4 默认开启 thinking，可通过 `thinking.type=enabled|disabled` 控制。在 OpenAI SDK
中，该参数需通过 `extra_body` 传入。Thinking 模式不支持 `temperature`、`top_p`、
`presence_penalty` 和 `frequency_penalty`；为了兼容既有软件，传入这些参数不会报错，但也
不会生效。

Thinking 输出位于 `reasoning_content`，最终答案位于 `content`。无工具的普通多轮会话不必
回传旧推理内容，回传也会被忽略；如果请求携带 tools，则后续工具轮次必须完整回传
`reasoning_content`，否则 API 返回 400。

SourceTrace 的建议：

- 结构化规划、证据判断和引用修复显式使用 `thinking.type=disabled`，避免依赖默认值变化，
  并减少推理 token 挤占 JSON 输出预算的风险。
- 结构化解析只读取 `content`，绝不把 `reasoning_content` 当成答案或事实。
- 流式回答默认显式关闭 thinking；若部署者显式开启，解析器仍只把 `content` 发送为答案 delta，
  推理内容既不持久化也不进入引用校验。
- Thinking 模式下不要依赖 `temperature=0` 获得确定性，因为官方说明该参数不生效。

来源：[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)，访问日期：2026-08-12。

## 流式回答与连接生命周期

### 官方契约

设置 `stream=true` 后，响应为 `text/event-stream`。普通数据通过 `data:` SSE event 发送；
中间 chunk 的 `finish_reason` 为 `null`，终止 chunk 才携带实际原因，消息流最后以
`data: [DONE]` 结束。Thinking 模式下 `delta.reasoning_content` 和 `delta.content` 分开出现，
两者均可能为空。

如果设置 `stream_options.include_usage=true`，`[DONE]` 之前会出现一个 `choices=[]` 的 usage
chunk；其他 chunk 的 `usage` 为 `null`。解析器不能假设每个 chunk 都有 choice。

请求排队时，DeepSeek 会向流式连接发送 SSE 注释 `: keep-alive`；非流式连接会收到空行。
这些都不是响应数据，客户端自定义解析器必须忽略。若请求在 10 分钟后仍未开始推理，服务端
会关闭连接。

来源：

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：2026-08-12。
- [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)，访问日期：2026-08-12。
- [FAQ](https://api-docs.deepseek.com/faq/)，访问日期：2026-08-12。

### SourceTrace 解析要求

1. 忽略空行、SSE 注释和不含 `data:` 的行。
2. 忽略正常的空 delta 和 usage-only chunk。
3. 只向用户发出 `delta.content`；不暴露或持久化 `reasoning_content`。
4. 收到非 `stop` 终态时丢弃未验证的完整答案，按上表分类。
5. 只有明确 `stop` 或协议允许的完整终止路径才可进入最终引用校验；在 `[DONE]` 或终态前
   断连不能视为完成。
6. 用户取消或浏览器断开时关闭上游响应流；已经展示但未通过门禁的 delta 不持久化。

连接在任何正文输出前中断时，可以在总 deadline 和重试预算内重试一次；正文已经输出后自动
重试会在界面制造重复或拼接内容，不应直接重发。这个策略是 SourceTrace 的工程决策。

## HTTP 错误、速率限制与重试

DeepSeek 官方错误表列出：

| HTTP 状态 | 官方含义 | 官方建议或 SourceTrace 判断 |
| --- | --- | --- |
| 400 | 请求体格式无效 | 按错误提示修改请求；相同请求不自动重试 |
| 401 | API key 错误 | 检查密钥；不自动重试 |
| 402 | 余额不足 | 充值；不自动重试 |
| 422 | 参数无效 | 修改参数；相同请求不自动重试 |
| 429 | 请求过快或超过并发 | 官方要求合理降速；可在预算内退避后有限重试 |
| 500 | 服务端错误 | 官方建议短暂等待后重试 |
| 503 | 服务过载 | 官方建议短暂等待后重试 |

当前账户级并发限制为 Flash 2500、Pro 500，并在同一账户的所有 API key 之间合并统计。
一个请求从发出到完整响应结束都占用一个并发名额，超过限制返回 429。

SourceTrace 应将 429、500、503、网络断连、读超时和
`insufficient_system_resource` 纳入统一的瞬态故障策略：指数退避、随机抖动、尊重响应中的
`Retry-After`（若存在）、最多一到两次并受总 deadline 限制。聊天补全重发可能重复计费，
因此每次尝试应记录安全的 attempt、HTTP 分类、耗时和 usage，不记录密钥、提示正文、响应正文
或完整文档内容。

官方没有给出客户端 connect/read timeout 的固定秒数，也没有规定最大重试次数。客户端应区分
连接超时、读取超时和应用总 deadline；由于排队阶段存在空行或 keep-alive，不能仅因长时间没有
JSON 数据就判定服务死亡。

来源：

- [Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)，访问日期：2026-08-12。
- [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)，访问日期：2026-08-12。

## 上下文与输出预算

API Reference 定义 `max_tokens` 为本次生成的最大 token 数；输入 token 和生成 token 总量不能
超过模型上下文长度。模型页当前标注上下文 1M、最大输出 384K。`finish_reason=length` 同时可能
来自输出上限或上下文上限，因此不能看到 `length` 就一律增加 `max_tokens`。

SourceTrace 应在请求前建立明确预算，保证输入、证据和预留输出都在上下文范围内；结构化任务的
输出预算应覆盖完整 JSON Schema，但不应直接使用供应商最大值。评测记录可以保存
`prompt_tokens`、`completion_tokens` 和 `reasoning_tokens` 等数值与供应商模型身份，但不能保存
完整提示词或回答正文作为错误日志。

来源：

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：2026-08-12。
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，访问日期：2026-08-12。
- [Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)，访问日期：2026-08-12。

## 与 SourceTrace 当前实现的差距

对 `apps/api/src/sourcetrace/rag/llm.py` 的检查显示。第 1 至 6 项已在 2026-08-12 的后续实现中
解决；第 7、8 项共同构成仍待独立处理的超时语义问题：

1. 流式解析已经忽略非 `data:` 行，因此能够跳过 keep-alive 注释；也能跳过空 choice 的 usage
   chunk，并对输出前的 `RemoteProtocolError` 做一次重试。**已解决**：现在还要求明确的
   `finish_reason=stop`，单独的 `[DONE]` 不再绕过完整性门禁。
2. 流式路径在收到 `[DONE]` 时直接返回，没有验证此前是否收到 `finish_reason=stop`；异常的
   `[DONE]` 可能绕过完整性门禁。其他非 `stop` 终态又被统一映射为
   `LLM_INCOMPLETE_RESPONSE`，缺少五类安全原因。**已解决**：流式和非流式路径均分类五种
   官方终态；只有首次、尚未输出正文的 `insufficient_system_resource` 会重试一次。
3. 非流式结构路径当前把所有非 `stop` 终态作为普通 `ValueError`，最终统一映射为
   `LLM_INVALID_RESPONSE`，这正是本次失败无法判断是否可恢复的原因。**已解决**：固定安全原因
   不包含响应正文或未知供应商原值；`length` 仍直接拒绝，待建立输出预算后再决定是否允许调整。
4. 结构化路径已有 JSON Output、非空检查和一次空内容重试。**已解决**：检索规划、槽位细化、
   证据判断和引用修复提示词均明确要求 JSON，并包含与各自 Schema 对齐的完整示例。
5. 配置支持显式关闭 thinking。**已解决**：结构化调用默认显式发送
   `response_format.type=json_object`、`thinking.type=disabled` 和有限的
   `max_tokens=2048`；最终流式回答也通过独立配置默认发送 `thinking.type=disabled`。
6. HTTP 错误目前大多统一映射为供应商不可用；需要按状态码区分配置错误、余额、限流和瞬态错误，
   但对外仍只暴露安全问题详情。**已解决**：400、401、402 和 422 立即安全失败；429、500、
   503、单次请求超时及网络/协议错误在应用总 deadline 内最多重试一次。流式路径仅在尚未输出
   正文时重试，固定原因不包含供应商响应正文。
7. 当前单一 timeout 包住完整请求。后续应在不破坏应用总 deadline 的前提下明确 connect/read
   语义，并为保活和供应商排队设计契约测试。
8. 当前本地 `LLM_TIMEOUT_SECONDS=60`，而官方说明请求可能通过空行或 SSE 注释保持连接，并在
   尚未开始推理 10 分钟后才由服务端关闭。SourceTrace 不必等待到官方上限，但必须明确 60 秒是
   自己的成本与体验 deadline，并避免把仍有保活的排队请求误分类成供应商格式错误。

## 推荐实施顺序

1. **分类供应商终态（已完成）**：实现五类 `finish_reason` 安全分类；只对尚未输出正文的
   `insufficient_system_resource` 做一次有界重试，`length` 在预算策略完成前保持安全拒绝。
2. **完善供应商错误边界（已完成）**：区分 400、401、402、422、429、500、503 与网络异常；
   只有瞬态错误进入有界退避重试。
3. **固定结构化请求契约（已完成）**：显式关闭 thinking，确保 prompt 包含 JSON 指令和精确
   示例，保持非空、解码和 Schema 三重验证，并为结构化输出设置可配置的有限预算。
4. **补齐流式契约测试（已完成）**：覆盖 keep-alive、`finish_reason=null`、usage 空 choice、
   reasoning content、五类终态、`[DONE]` 前断连、输出前后断连及终态后异常正文。
5. **再运行真实评测**：确认供应商异常不会被错误计入 RAG 指标；报告保存安全失败分类和尝试次数，
   不保存密钥或被拒绝的响应正文。

建议的供应商 contract fake 测试至少覆盖：

- 五种官方 `finish_reason`。
- `stop` 加空 `content`。
- `length` 加截断 JSON。
- 普通流式 chunk 的 `finish_reason=null`。
- `: keep-alive` 注释和空行。
- `stream_options.include_usage` 产生的空 choices chunk。
- 在终态和 `[DONE]` 前连接中断。
- HTTP 400、401、402、422、429、500 和 503。

## 官方资料索引

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期：2026-08-12。
- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)，访问日期：2026-08-12。
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)，访问日期：2026-08-12。
- [Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)，访问日期：2026-08-12。
- [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)，访问日期：2026-08-12。
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，访问日期：2026-08-12。
- [Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)，访问日期：2026-08-12。
- [FAQ](https://api-docs.deepseek.com/faq/)，访问日期：2026-08-12。
- [Change Log](https://api-docs.deepseek.com/updates/)，访问日期：2026-08-12。
