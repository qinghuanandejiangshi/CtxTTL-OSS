# Responses API 透明代理

[English](../responses-api-proxy.md) · [中文文档中心](README.md)

CtxTTL 新增 OpenAI 兼容的 Responses 端点：

```text
POST /v1/responses
GET  /v1/models
```

它与 Chat Completions 代理共用生命周期投影、确定性选择、存储、Trace 和身份规则。Responses
协议转换位于独立适配器中，不改变编译策略。

## 数据链路

```text
Agent / Codex
    -> POST /v1/responses
    -> 显式身份解析
    -> Responses 协议适配器
    -> 生命周期感知编译器
    -> 上游 POST /responses
    -> 原样转发 JSON 或 SSE
```

未知顶层字段、工具、模型设置和供应商扩展字段会被保留。显式 `input` 消息可以参与预算选择；
推理、函数调用、函数返回、Computer Use 等非消息项作为必须保留的原子单元处理，不解释、
不拆分、不局部删除，并在编译后原样恢复。

`instructions` 保持在原生字段中，同时以强制输入计入预算。选中的生命周期上下文会成为显式
system 输入。内部适配标记绝不会发送给上游。

## 显式状态要求

CtxTTL 只能删除或替换自己能够观察到的状态。在 compile 模式下，包含以下任一上游隐藏引用的
请求会失败关闭并返回 HTTP 400：

- `previous_response_id`；
- `conversation`；
- 托管 `prompt` 引用。

这些字段可能让上游自动加入代理无法检查的历史或 Prompt。如果静默接受，就无法证明生命周期
规则真正生效。调用方应发送包含完整显式 `input` 的无状态请求。若设置
`CTXTTL_MISSING_SESSION_BEHAVIOR=passthrough`，缺少 CtxTTL Session 身份的请求会原样转发，并
明确返回 `X-CtxTTL-Mode: passthrough`。

## 身份与响应 Header

Responses 端点沿用 Core 的身份协议：

```text
X-CtxTTL-Session-ID: compile 模式必填
X-CtxTTL-User-ID: 可选
X-CtxTTL-Task-ID: 可选
X-CtxTTL-Agent-ID: 可选的 Agent 私有作用域
X-CtxTTL-Project-ID: 可选的项目共享与 Task 命名空间
X-CtxTTL-Turn-ID: Turn 作用域和轮次租约必填
X-CtxTTL-Request-ID: 可选的幂等/关联标识
```

编译后的响应包含 `X-CtxTTL-Trace-ID`、`X-CtxTTL-Request-ID`、
`X-CtxTTL-Mode: compile` 以及编译/上游耗时 Header。上游响应字节、状态码、Content-Type、
Request ID、限流 Header 和 SSE 事件不会被改写。

成功完整消费响应后，Execution Trace 会在供应商提供时记录输入、缓存命中输入、输出 Token，
同时记录编译耗时、上游首响应耗时、流式耗时、代理总耗时、状态、结果和协议类型。Execution
Telemetry 不记录原始 Prompt 和回答内容。

对话记忆优先从终止响应快照归档完整 assistant 消息。如果供应商在该快照中省略输出，适配器
会回退到协议中的 completed output-item 事件。工具调用和工具结果仍保持为不可拆分的原子项，
并在 Agent 的下一次显式请求中原样保留。

## Codex 配置

Codex 自定义模型供应商必须写在用户级 `~/.codex/config.toml`，不能写在项目内的
`.codex/config.toml`。本地最小配置如下：

```toml
model = "your-openai-compatible-model"
model_provider = "ctxttl"

[model_providers.ctxttl]
name = "CtxTTL local gateway"
base_url = "http://127.0.0.1:8765/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
env_http_headers = {
  "X-CtxTTL-Session-ID" = "CTXTTL_SESSION_ID",
  "X-CtxTTL-Agent-ID" = "CTXTTL_AGENT_ID",
  "X-CtxTTL-Project-ID" = "CTXTTL_PROJECT_ID",
  "X-CtxTTL-Task-ID" = "CTXTTL_TASK_ID"
}
```

如果 Core 已设置 `CTXTTL_UPSTREAM_API_KEY`，可以省略 `env_key`；否则 Codex 把密钥发给本地
网关，由 Core 转发到上游。启动 Codex 前设置稳定、不透明的身份：

```powershell
$env:CTXTTL_SESSION_ID = "codex-session-001"
$env:CTXTTL_AGENT_ID = "codex-worker"
$env:CTXTTL_PROJECT_ID = "repository-001"
$env:CTXTTL_TASK_ID = "repository-task-001"
codex
```

身份中不要放入用户数据、仓库秘密或 Prompt 内容。Codex 升级后应先检查第一条请求的 Trace；
如果客户端改为隐藏的 `previous_response_id` 历史，网关会按设计失败关闭。

## 安全与兼容边界

Core 默认只监听 loopback。身份 Header 是隔离坐标，不是认证。互联网或多租户部署仍需在 Core
前增加 TLS、认证、授权、限流、租户凭据、存储隔离和保留策略。

第一阶段支持缓冲 HTTP 与 SSE 的 `POST /responses`。Retrieve、Cancel、Delete、Input Items
列表、WebSocket、后台结果轮询和供应商托管 Conversation 暂未代理。

`GET /v1/models` 是供 Codex 能力发现使用的只读兼容转发端点。查询参数、供应商认证、响应
状态码、Content-Type、Request ID 和响应体会原样转发，不触发身份解析、生命周期编译、归档或
Trace。

## 协议参考

- [OpenAI Responses 创建接口](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [OpenAI Responses 流式输出指南](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Codex 配置参考](https://developers.openai.com/zh-Hans/docs/config-file/config-reference)
