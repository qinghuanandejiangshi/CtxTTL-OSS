# 接入验收案例

[English](../integration-cases.md)

本页只记录公开产品契约的验收范围，不公开私有任务、轨迹、供应商载荷、对比结果或研究协议。

## OpenAI 兼容数据面

现有 Agent 只需替换 Base URL，并附加稳定身份 Header，即可让显式无状态的 Chat Completions
或 Responses 请求经过 CtxTTL。契约测试覆盖普通与流式转发、模型发现、生命周期编译、Trace
写入，以及对隐藏上游状态的失败关闭行为。

## MCP 控制面

独立打包的 MCP 服务暴露显式生命周期与 Trace 操作，Core 无需依赖 MCP SDK。MCP 负责管理
状态；HTTP 数据面负责对实际模型请求强制执行编译。

## 通用多 Agent 契约

公开 HTTP 契约测试使用两个不同 Session 与 Agent ID。Agent 私有上下文保持隔离，Task 与
Project 上下文可以有意共享；测试不依赖框架对象或运行时专用编译路径。

这些检查只证明 Wire Contract 与隔离行为，不构成普遍的 Token、延迟、成本或回答质量优势。
新运行时应按照[快速接入指南](quick-agent-integration.md)执行相同的黑盒验收。
