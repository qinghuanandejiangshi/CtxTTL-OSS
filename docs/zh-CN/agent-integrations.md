# 通用 Agent 接入协议

[English](../agent-integrations.md)

CtxTTL 接入的是模型上下文边界，而不是某个 Agent 循环。只要运行时能够发送 HTTP 请求，或能
配置 OpenAI 兼容的 Base URL，就使用同一套协议；不需要为每一个框架编写专用适配器。

```text
任意 Agent 运行时
    -> 标准 CtxTTL 身份 Header
    -> /v1/responses | /v1/chat/completions | /v1/context/compile
    -> 同一个生命周期编译器和状态模型
    -> 上游模型或编译后的载荷
```

协议刻意小于任何框架 SDK。规划、工具执行、模型选择和框架对象都留在 Core 之外，避免把
LangGraph、OpenClaw、Codex 或自研运行时的概念写入生命周期策略。

## 一套身份契约

所有数据面与控制面操作使用以下不透明坐标：

| Header / MCP 参数 | 必填 | 所有权边界 |
| --- | ---: | --- |
| `X-CtxTTL-Session-ID` / `session_id` | 是 | 一次对话或执行 Session |
| `X-CtxTTL-Agent-ID` / `agent_id` | 否 | 单个 Agent 角色或 Worker 的私有状态 |
| `X-CtxTTL-Project-ID` / `project_id` | 否 | 一个项目内共享的状态 |
| `X-CtxTTL-Task-ID` / `task_id` | 否 | 共同处理一个任务的 Agent 共享状态 |
| `X-CtxTTL-User-ID` / `user_id` | 否 | 同一用户跨 Session 的状态 |
| `X-CtxTTL-Turn-ID` / `turn_id` | 否 | 当前轮状态与可安全重试的轮次租约 |
| `X-CtxTTL-Request-ID` | 否 | 幂等与关联标识 |

调用方提供稳定且不含秘密的 ID，CtxTTL 不从 Prompt 文本猜测所有权。两个 Agent 使用相同的
Project 和 Task 坐标时，可以访问共享的 Task/Project 状态，但彼此的 `agent` 与 `session`
作用域仍然隔离。不传 Agent/Project ID 时，原有 Session/Task/User 行为保持兼容。

可选 MCP 控制面也使用同样的坐标，因此运行时可通过 MCP 显式写入或结束状态，HTTP 透明数据面
无需维护转换表即可编译这些状态。

## 三个通用入口

| 需求 | 接口 | 调用模型 |
| --- | --- | ---: |
| Responses 兼容透明网关 | `POST /v1/responses` | 是 |
| Chat Completions 兼容透明网关 | `POST /v1/chat/completions` | 是 |
| 只编译或配合自有 Provider Client | `POST /v1/context/compile` | 否 |

前两个接口接收普通供应商请求。只编译接口返回 `payload`、`trace_id`、`request_id` 以及 Token/
选择指标，再由调用方把返回载荷发给模型。三个入口共享身份解析、生命周期投影、编译器、存储与
Trace 结构。

最小 Chat Completions 示例：

```http
POST /v1/chat/completions
X-CtxTTL-Session-ID: run-42
X-CtxTTL-Agent-ID: researcher
X-CtxTTL-Project-ID: project-alpha
X-CtxTTL-Task-ID: investigate-regression
X-CtxTTL-Turn-ID: turn-3
Content-Type: application/json

{"model":"your-model","messages":[{"role":"user","content":"继续调查"}]}
```

切换 Agent 平台不改变接口、Body Schema 或 CtxTTL 语义，平台只需把这些值作为请求 Header
传入。若平台无法设置 Header，则在原有模型客户端之前调用只编译接口。

## 多 Agent 共享模型

根据状态应对谁可见来选择 Scope：

| Scope | 可见范围 |
| --- | --- |
| `turn` | 一个逻辑轮次 |
| `session` | 一次对话或执行 Session |
| `agent` | 同一 User/Project 命名空间内该 Agent 的多个 Session |
| `task` | 同一 User/Project 命名空间内具有相同 Task 的所有 Agent |
| `project` | 同一 User/Project 命名空间内的所有 Agent |
| `user` | 该用户的所有项目与 Session |

这些坐标是能力选择器，不是身份认证。公网或多租户部署必须在请求进入 Core 前完成认证，并
授权调用者可以声明哪些 ID；应由网关写入可信 Header，而不是相信任意互联网客户端。

## 接入验收契约

一个 Agent 平台在不向 Core 添加框架代码的前提下通过以下黑盒检查，即可认为协议兼容：

1. 同一 Session 的编译结果确定，并产生可访问 Trace。
2. 两个不同 Agent ID 无法读取彼此的 `agent` 作用域状态。
3. 两者都能读取共同 Task 或 Project 中明确共享的状态。
4. 已替代、撤回和过期的状态不会进入透明接口或只编译接口。
5. 工具调用组保持原子性、流式协议有效、上游供应商字段不丢失。
6. 缺失显式状态或必需上下文无法放入预算时失败关闭。
7. 供应商返回数据时，真实遥测记录输入、缓存输入、输出、耗时、状态、重试和结果。

通过这些检查可以证明协议兼容与隔离，但不能单独证明 Token、延迟、成本或回答质量改善；这些
结论需要另行设计并复核的评测。

## Python 应用端口

进程内 Python 系统可以依赖 `AgentContextPort` 而不是 HTTP。`AgentContextRequest` 使用同样的
Session、Agent、Project、Task、User、Turn 和 Request 坐标，默认实现是
`LifecycleAwareAgentContext`。这是应用端口而不是框架适配器，调用方不应复制编译或持久化策略。

## 旧版 OpenClaw 兼容入口

现有 `/v1/integrations/openclaw/*` 接口仅为向后兼容而保留。它把 OpenClaw 的历史字段做稳定
哈希并翻译为通用契约，然后调用相同应用服务。新接入应使用通用接口；该兼容入口不是架构扩展点，
也不需要为每个新 Agent 框架复制一份。

OpenClaw 专用的失效轮次标记只在旧接口继续支持。通用运行时应通过 `/v1/context/items` 或 MCP
表达持久生命周期变化，而不是在 Prompt 中嵌入平台控制语法。
