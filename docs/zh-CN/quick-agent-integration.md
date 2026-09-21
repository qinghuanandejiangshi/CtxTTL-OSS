# 快速接入现有 Agent

[English](../quick-agent-integration.md)

CtxTTL 位于 Agent 与模型供应商之间，不替换 Agent 循环、规划器、工具或模型 SDK，也不需要为
每一个框架编写 CtxTTL 专用适配器。

## 选择一条数据路径

| 现有 Agent 能力 | CtxTTL 路径 | 所需修改 |
| --- | --- | --- |
| 可配置 OpenAI 兼容 Base URL 和请求 Header | 透明代理 | 修改 Base URL 并添加身份 Header |
| 可在模型请求前插入 Hook | 只编译 | 把供应商载荷 POST 到 `/v1/context/compile` |
| 两者都不支持 | 外部网关/插件 Hook | 为 Agent 增加一个标准化拦截点 |

MCP 是三条路径都可使用的可选控制面。它让 Agent 显式写入、替代、撤回、过期、查询和编译
状态，但 MCP 本身不会拦截模型调用。

## 路径 A：OpenAI 兼容透明代理

先在 `.env` 中配置上游供应商并启动 Core：

```dotenv
CTXTTL_UPSTREAM_BASE_URL=https://your-provider.example/v1
CTXTTL_UPSTREAM_API_KEY=replace-me
```

```powershell
.\.venv\Scripts\python.exe -m ctxttl
```

macOS/Linux 激活虚拟环境后运行 `python -m ctxttl`。

将 Agent 的模型 Base URL 改为：

```text
http://127.0.0.1:8765/v1
```

发送普通模型请求时附带稳定身份 Header：

```text
X-CtxTTL-Session-ID: run-42
X-CtxTTL-Agent-ID: researcher
X-CtxTTL-Project-ID: project-alpha
X-CtxTTL-Task-ID: investigate-regression
X-CtxTTL-Turn-ID: turn-3
```

Responses 客户端使用 `POST /v1/responses`，Chat Completions 客户端使用
`POST /v1/chat/completions`。JSON 与 SSE 保持原协议格式，CtxTTL 身份 Header 只在本地消费，
不会转发给模型供应商。

### Python/OpenAI 兼容客户端

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="local-gateway-key",
    default_headers={
        "X-CtxTTL-Session-ID": "run-42",
        "X-CtxTTL-Agent-ID": "researcher",
        "X-CtxTTL-Project-ID": "project-alpha",
        "X-CtxTTL-Task-ID": "investigate-regression",
    },
)
```

JavaScript 客户端和提供 OpenAI 兼容供应商设置的 Agent 框架也使用相同配置模式。

## 路径 B：只编译 Hook

如果 Agent 自己管理供应商客户端，在调用模型前先调用 CtxTTL：

```python
import httpx

identity = {
    "X-CtxTTL-Session-ID": "run-42",
    "X-CtxTTL-Agent-ID": "researcher",
    "X-CtxTTL-Project-ID": "project-alpha",
    "X-CtxTTL-Task-ID": "investigate-regression",
}
provider_payload = {
    "model": "your-model",
    "messages": [{"role": "user", "content": "继续调查"}],
}

compiled = (
    httpx.post(
        "http://127.0.0.1:8765/v1/context/compile",
        headers=identity,
        json=provider_payload,
    )
    .raise_for_status()
    .json()
)

# 使用 Agent 原有供应商客户端发送 compiled["payload"]。
print(compiled["trace_id"], compiled["metrics"])
```

编译后不能继续发送原始载荷，否则会绕过生命周期约束。只编译接口本身不调用模型。

## 可选 MCP 控制面

支持 MCP 的 Agent 可以安装 `packages/ctxttl-mcp` 并使用七个显式生命周期工具。MCP 工具应使用
与模型请求完全相同的 `session_id`、`agent_id`、`project_id` 和 `task_id`，这样通过 MCP 写入的
状态可立即被透明代理或只编译数据面读取。

安装与安全设置见 [MCP 接入](mcp-integration.md)。

## Agent 应写入哪个 Scope？

| 期望生命周期或可见范围 | Scope |
| --- | --- |
| 仅当前轮 | `turn` |
| 仅当前对话/执行 | `session` |
| 单个 Agent 角色跨 Session 私有 | `agent` |
| 同一任务的多个 Agent 共享 | `task` |
| 项目内共享 | `project` |
| 同一用户跨 Session 共享 | `user` |

事实变化时使用生命周期操作：新决策用 `supersede` 替代旧决策，撤销内容用 `retract`，临时状态
结束用 `expire`。CtxTTL 无法从任意 Prompt 文本中可靠猜测这些业务事件。

## 接入验收

投入生产前至少验证一条完整链路：

1. 响应包含 `X-CtxTTL-Mode: compile`、`X-CtxTTL-Request-ID` 和
   `X-CtxTTL-Trace-ID`。
2. Trace 只能通过预期身份坐标访问。
3. 切换 Agent ID 后看不到原 Agent 私有状态。
4. 第二个已授权 Agent 使用相同共享坐标时能够看到 Task/Project 状态。
5. 已替代或过期的测试状态不进入编译后载荷。
6. 该 Agent 使用的真实模型协议中，流式响应和工具调用组仍然有效。

身份 Header 是路由与隔离坐标，不是认证。公网或多租户部署必须在 Core 前增加 TLS、身份认证、
授权、限流和可信 Header 注入。
