# CtxTTL 身份协议

[English](../identity-protocol.md)

CtxTTL 在存储或读取上下文前必须知道请求的所有权边界，不会从消息文本推断身份。

## Header

| Header | 必填 | 含义 |
| --- | ---: | --- |
| `X-CtxTTL-Session-ID` | 是 | 对话或执行 Session 的隔离边界 |
| `X-CtxTTL-User-ID` | 否 | 跨 Session 的 User 作用域 |
| `X-CtxTTL-Task-ID` | 否 | Session 内或跨 Session 的 Task 作用域 |
| `X-CtxTTL-Agent-ID` | 否 | 单个 Agent 角色或 Worker 的私有作用域 |
| `X-CtxTTL-Project-ID` | 否 | 共享 Project 作用域与 Task 命名空间 |
| `X-CtxTTL-Turn-ID` | 否 | 逻辑轮次作用域和轮次租约计数 |
| `X-CtxTTL-Request-ID` | 否 | 幂等与请求关联 |

Header 名称可以配置。Header 值是不透明标识，只允许 1 至 128 个 ASCII 字母、数字、点、
下划线、冒号或连字符。

若 `X-CtxTTL-User-ID` 与 OpenAI 兼容请求中的 `user` 字段同时存在，以显式 Header 为准；
`user` 字段不会被当作 Session ID。

Turn ID 是显式逻辑标识，不是消息数组位置。网络重试重复使用同一个 Turn ID，不会再次消耗
轮次租约。

Agent 与 Project 坐标属于协议概念，而不是某个框架类型。`agent` 作用域用于隔离同一
User/Project 命名空间中某个 Agent 跨 Session 的状态；持有相同坐标的不同 Agent 可以有意共享
`task` 与 `project` 作用域。身份值用于选择所有权边界，不负责认证调用者。

## 缺失 Session 的行为

默认行为是 `reject`，透明代理会返回 HTTP 400。运维方可以设置
`CTXTTL_MISSING_SESSION_BEHAVIOR=passthrough`；此时缺失 Session 的请求原样发送给上游，不进行
上下文编译或记忆写入。这个显式行为可避免意外的跨 Session 记忆污染。
