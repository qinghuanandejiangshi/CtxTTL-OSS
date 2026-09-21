# MCP 控制面接入

[English](../mcp-integration.md)

`packages/ctxttl-mcp` 通过 Streamable HTTP 向 Codex 等 MCP Host 暴露 CtxTTL 生命周期操作。
它是独立打包的控制面：

```text
MCP Host -> ctxttl-mcp -> CtxTTL 公开 HTTP 契约 -> CtxTTL Core
```

Core 不依赖 MCP SDK。适配器可以独立升级、重启或替换，不改变编译器、存储、Chat Completions
代理和通用 Agent 端口。

## 本地启动

先启动 Core：

```powershell
.\.venv\Scripts\python.exe -m ctxttl
```

安装并启动 MCP 适配器：

```powershell
.\.venv\Scripts\python.exe -m pip install -e packages\ctxttl-mcp
$env:CTXTTL_MCP_CORE_URL = "http://127.0.0.1:8765"
$env:CTXTTL_MCP_BEARER_TOKEN = "replace-with-a-long-random-token"
.\.venv\Scripts\ctxttl-mcp.exe
```

在 Codex 中注册：

```powershell
$env:CTXTTL_MCP_TOKEN = "replace-with-the-same-token"
codex mcp add ctxttl --url http://127.0.0.1:8766/mcp --bearer-token-env-var CTXTTL_MCP_TOKEN
codex mcp list
```

令牌应保存在环境变量中，不要写入仓库配置。

## 工具契约

| 工具 | 用途 |
| --- | --- |
| `ctxttl_context_assert` | 创建持久或有租约的事实、决策、约束、任务状态。 |
| `ctxttl_context_supersede` | 原子替换当前身份可访问的活动条目。 |
| `ctxttl_context_retract` | 撤回不再真实或不再获得授权的条目。 |
| `ctxttl_context_expire` | 结束已经完成预期生命周期的条目。 |
| `ctxttl_context_list` | 查询显式身份坐标可以访问的状态。 |
| `ctxttl_compile_request` | 编译 Chat Completions 请求，但不调用模型。 |
| `ctxttl_trace_get` | 检查一条当前身份可访问的编译 Trace。 |

每个工具都要求稳定、不透明的 `session_id`，并接受与 HTTP 数据面相同的可选 `agent_id`、
`project_id`、`task_id`、`user_id` 和 `turn_id` 坐标。身份字段中不要放入 Prompt、邮箱、频道
名称、访问令牌或其他敏感内容。Agent、Project、Task、User 与 Turn 作用域必须提供对应身份坐标。

## 运行边界

MCP 是显式状态控制面；它不能保证 Host 在每次推理前都调用工具，也不会替换 Host 内部的上下文
窗口。透明强制执行由独立且有明确边界的 [Responses API 数据面](responses-api-proxy.md)负责。
`ctxttl_compile_request` 不调用模型，但会记录编译 Trace/归档并观察本轮生命周期状态，因此它
不是只读预览。

适配器输出结构化诊断日志，但不会记录原始身份和上下文值。默认监听 `127.0.0.1:8766`；未配置
Bearer Token 时会拒绝绑定非 loopback 地址。静态 Bearer Token 适用于受控本地试点；互联网或
多租户部署还需要 TLS、OAuth 或网关凭据、授权、限流和租户隔离。
