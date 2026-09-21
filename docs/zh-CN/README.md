# CtxTTL 中文文档

[English](../README.md) · [仓库首页](../../README.zh-CN.md)

CtxTTL 是生命周期感知上下文中间件，而不是完整 Agent 框架。第一次接触项目时，请先阅读
仓库首页并运行最小示例；本文档中心用于了解架构、接入方式、验证方法和能力边界。

## 中文核心文档

- [架构与依赖规则](architecture.md)
- [通用 Agent 接入协议](agent-integrations.md)
- [快速接入现有 Agent](quick-agent-integration.md)
- [实际接入案例](integration-cases.md)
- [身份协议](identity-protocol.md)
- [MCP 控制面接入](mcp-integration.md)
- [Responses API 透明代理](responses-api-proxy.md)
- [已知限制](known-limitations.md)
- [公开产品路线图](roadmap.md)

## 深入技术文档

以下页面当前以英文作为权威技术版本；代码符号、API 字段和命令保持英文不翻译：

- [上下文编译器](../context-compiler.md)
- [上下文状态与存储](../context-state-storage.md)
- [对话归档与检索](../conversation-memory.md)
- [Chat Completions 代理](../chat-completions-proxy.md)
- [结构化写入](../structured-ingestion.md)
- [身份协议](../identity-protocol.md)
- [可观测性](../observability.md)
- [模型评测框架](../model-evaluation.md)
- [Benchmark 指南](../benchmarks.md)

## 项目治理

- [仓库与证据策略](../repository-policy.md)
- [安全策略](../../SECURITY.md)
- [贡献规范](../../CONTRIBUTING.md)
- [多语言文档规范](../i18n.md)

`docs/locales.toml` 定义核心文档的语言包映射，CI 会检查每种已声明语言是否缺页。尚未翻译的
深入技术页面以英文为准，避免翻译滞后造成接口含义分叉。
