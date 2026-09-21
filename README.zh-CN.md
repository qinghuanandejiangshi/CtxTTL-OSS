<div align="center">
  <img src="docs/assets/readme/hero.svg" alt="CtxTTL Core——生命周期感知上下文中间件" width="100%" />

  <br />

  [![CI](https://github.com/qinghuanandejiangshi/CtxTTL-OSS/actions/workflows/ci.yml/badge.svg)](https://github.com/qinghuanandejiangshi/CtxTTL-OSS/actions/workflows/ci.yml)
  [![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![Version](https://img.shields.io/badge/version-0.2.1-8b7cff)](CHANGELOG.md)
  [![License](https://img.shields.io/badge/license-Apache--2.0-4de2c5)](LICENSE)

  **不要再把所有历史状态都交给模型；只编译当前仍然有效的状态。**

  [快速开始](#快速开始) · [工作原理](#工作原理) · [接入验收](#接入验收) · [中文文档](docs/zh-CN/README.md) · [English](README.md)
</div>

---

CtxTTL Core 是面向长时运行 LLM Agent 的生命周期感知上下文中间件。它为事实、决策、约束、
纠正和临时指令建立显式生命周期，然后从活动状态中编译出确定性、有预算上限的模型请求。

它**不是另一个 Agent 框架**。你可以继续使用现有规划器、工具、模型供应商和检索系统，只需
通过通用 OpenAI 兼容协议、只编译 HTTP 接口、MCP 控制面或供应商无关的 Python 端口，只在
上下文边界接入一次 CtxTTL。

## 问题是什么

对话记录是审计历史，但不一定是当前有效状态。

```text
第 4 轮   “使用 SQLite。”                  ← 曾经有效
第 19 轮  “生产环境必须使用 PostgreSQL。”  ← 替代第 4 轮
第 37 轮  “我们最终选择了什么数据库？”
```

同时发送两条决策，相当于让模型概率性地判断生命周期。CtxTTL 显式记录纠正，在事务中投影活动
状态，并在第 37 轮只编译适用证据。

| 持续增长的历史 | CtxTTL |
| --- | --- |
| 新旧决策同时存在 | `supersede` 后只保留一个活动决策 |
| 临时指令长期残留 | 时间租约与轮次租约显式到期 |
| 检索可能召回陈旧文本 | 检索证据与权威状态隔离 |
| Token 不够时静默删内容 | 硬约束无法容纳时失败关闭 |
| Agent 自己维护记忆逻辑 | 适配器调用供应商无关端口 |
| 失败后难以解释 | 每次编译产生可重放 Trace |

## 工作原理

<p align="center">
  <img src="docs/assets/readme/lifecycle.svg" alt="CtxTTL 生命周期投影与编译流程" width="100%" />
</p>

核心流程保持精简：

1. **记录** `assert`、`supersede`、`retract`、`expire` 等生命周期事件。
2. **投影** 事务一致的活动状态。
3. **过滤** Session、Task、User、Turn、作用域、权威性与适用性。
4. **编译** 硬约束、协议相关近期消息和预算内的相关软证据。
5. **追踪** 编译决策，支持检查与精确重放。

## 快速开始

### 1. 安装

<details open>
<summary><strong>Windows PowerShell</strong></summary>

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

</details>

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

</details>

在 `.env` 中设置 `CTXTTL_UPSTREAM_API_KEY` 和上游模型配置，然后启动：

```powershell
.\.venv\Scripts\python.exe -m ctxttl
```

macOS/Linux 激活环境后运行 `python -m ctxttl`。

### 2. 写入状态，然后提问

```python
import httpx

headers = {
    "X-CtxTTL-Session-ID": "demo-session",
    "X-CtxTTL-Agent-ID": "demo-agent",
    "X-CtxTTL-Project-ID": "demo-project",
    "X-CtxTTL-Task-ID": "demo-task",
}

with httpx.Client(base_url="http://127.0.0.1:8765", headers=headers) as client:
    client.post(
        "/v1/context/items",
        json={
            "kind": "decision",
            "scope": "task",
            "subject": "project.database",
            "value": "PostgreSQL",
            "reason": "production requirement",
        },
    ).raise_for_status()

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "your-model",
            "messages": [{"role": "user", "content": "Which database did we choose?"}],
        },
    )
    response.raise_for_status()
    print(response.json())
```

完整示例见 [`examples/quickstart.py`](examples/quickstart.py)。生命周期更新、身份 Header、流式
响应与重放分别见[结构化写入](docs/structured-ingestion.md)和
[Chat Completions 代理](docs/chat-completions-proxy.md)。

## 快速接入现有 Agent

CtxTTL 是一套协议边界，而不是一组框架专用适配器：

| 现有 Agent 提供的能力 | 接入方式 |
| --- | --- |
| OpenAI 兼容 Base URL 与自定义 Header | 指向 `http://127.0.0.1:8765/v1` |
| 模型客户端调用前 Hook | 调用 `POST /v1/context/compile`，再发送返回的载荷 |
| MCP | 增加可选生命周期控制面；透明强制执行仍使用上述一种数据路径 |

所有路径使用相同的稳定坐标：

```text
X-CtxTTL-Session-ID: run-42
X-CtxTTL-Agent-ID: researcher
X-CtxTTL-Project-ID: project-alpha
X-CtxTTL-Task-ID: investigate-regression
```

不同 Agent ID 可以共享 Task/Project 状态，同时隔离 Agent/Session 私有状态。新平台直接使用这些
标准 Header 和通用 `/v1/responses`、`/v1/chat/completions` 或 `/v1/context/compile` 接口，
不需要在 Core 中新增适配器。

Python、透明代理、只编译、MCP、Scope 选择和验收示例见
[快速接入现有 Agent](docs/zh-CN/quick-agent-integration.md)，完整隔离与安全边界见
[通用 Agent 协议](docs/zh-CN/agent-integrations.md)。

## 通过 MCP 接入 Codex

独立打包的 MCP 控制面使 Codex 可以显式管理生命周期状态，同时不让 Core 依赖 MCP SDK：

```powershell
.\.venv\Scripts\python.exe -m pip install -e packages\ctxttl-mcp
$env:CTXTTL_MCP_BEARER_TOKEN = "replace-with-a-long-random-token"
.\.venv\Scripts\ctxttl-mcp.exe
```

在另一个终端注册：

```powershell
$env:CTXTTL_MCP_TOKEN = "replace-with-the-same-token"
codex mcp add ctxttl --url http://127.0.0.1:8766/mcp --bearer-token-env-var CTXTTL_MCP_TOKEN
```

MCP 是显式状态控制面，不会拦截每一次模型请求。七个工具、安全边界、排错日志，以及它与独立
Responses API 数据面的区别见 [MCP 接入指南](docs/zh-CN/mcp-integration.md)。

## 让 Codex 模型调用经过 CtxTTL

新增的 `POST /v1/responses` 是面向 Codex 和其他 Responses 客户端的透明数据面。自定义供应商
配置使用 `base_url = "http://127.0.0.1:8765/v1"`、`wire_api = "responses"`，并通过环境变量
注入 CtxTTL 身份 Header。包含完整显式 `input` 的无状态请求会在到达上游前执行生命周期编译，
JSON 和 SSE 响应原样转发。独立的 `GET /v1/models` 会转发 Codex 能力发现请求，但不进入上下文
编译器。

compile 模式会有意拒绝通过 `previous_response_id`、托管 Conversation 或托管 Prompt 引用
隐藏上游历史的请求，避免产生“已经过滤但实际看不到历史”的虚假保证。完整 Codex 配置、兼容
边界、安全要求及 Token/耗时遥测见
[Responses 代理指南](docs/zh-CN/responses-api-proxy.md)。

## 当前能力

| 分层 | 能力 |
| --- | --- |
| 生命周期 | assert、supersede、retract、expire、时间租约、轮次租约 |
| 状态 | 事务化 SQLite 事件日志与活动状态投影 |
| 编译器 | 协议感知的近期保留、硬约束、软证据预算 |
| 记忆 | 对话归档与隔离的 FTS5 检索 |
| 接入 | 通用 Agent 身份协议、OpenAI 兼容数据面、Python 端口、MCP 控制面 |
| 可观测性 | Session 隔离 Trace，以及内容捕获下的精确重放 |
| 评测 | 无模型回归与可断点续跑的供应商无关模型评测框架 |

各模块通过端口和契约解耦，存储、检索、Token 估算、模型供应商和 Agent 运行时均可独立替换。
能够设置 HTTP Header 的平台直接使用同一个接口，不需要专用适配器。详见
[通用 Agent 协议](docs/zh-CN/agent-integrations.md)与 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 接入验收

| 案例 | 实际覆盖 | 结论边界 |
| --- | --- | --- |
| OpenAI 兼容 Agent 客户端 | 替换 Base URL、身份 Header、普通与流式请求 | 请求通过公开协议边界完成编译与转发 |
| Codex 本机接入 | Responses 数据面、MCP 控制面、模型发现、SSE、Trace 与执行遥测 | 接入成功；短 Smoke Session 不是 Token 性能实验 |
| 通用多 Agent 契约 | 两个 Agent、Agent 私有状态、Task/Project 共享状态、只使用公开 HTTP | 可复现测试中隔离与有意共享均通过 |

完整过程和限制见[实际接入案例](docs/zh-CN/integration-cases.md)。这些验收只证明协议与隔离
行为，不构成普遍的 Token、延迟、成本或模型质量优势结论。

## 确定性验证

无需 API Key 即可运行无模型 Benchmark：

```powershell
.\.venv\Scripts\python.exe -m ctxttl.benchmarks.cli benchmarks\ctxttlbench.jsonl
```

它测量生命周期选择和重放机制，不测量模型回答质量。

## 设计原则

- **先判断有效性，再计算相关性：** 已过期或被替代的记录不会因为匹配查询而复活。
- **显式优于猜测：** 关键纠正与约束使用类型化事件，而不是隐藏的摘要行为。
- **失败关闭：** 必需上下文无法放入预算时返回错误，而不是静默遗漏。
- **供应商无关：** 生命周期语义不依赖某个模型或 Agent 运行时。
- **结论有边界：** 工程测试、开发观察和正式发布结论严格区分。

CtxTTL 不声称自己是第一个 Prompt 压缩或 Agent 记忆系统。它聚焦于显式生命周期语义、
确定性投影与编译、可替换边界和可审计重放的完整工程实现。详见
[已知限制](docs/zh-CN/known-limitations.md)。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest tests packages\ctxttl-mcp\tests
.\.venv\Scripts\python.exe scripts\check_repository_hygiene.py
```

GitHub Actions 会在 Python 3.11、3.12 和 3.13 上运行相同检查。macOS/Linux 使用 `python`
和 `/` 路径分隔符。

## 文档

| 快速入口 | 深入了解 |
| --- | --- |
| [中文文档中心](docs/zh-CN/README.md) | [上下文编译器](docs/context-compiler.md) |
| [快速接入现有 Agent](docs/zh-CN/quick-agent-integration.md) | [实际接入案例](docs/zh-CN/integration-cases.md) |
| [通用 Agent 协议](docs/zh-CN/agent-integrations.md) | [状态与存储](docs/context-state-storage.md) |
| [Responses 代理](docs/zh-CN/responses-api-proxy.md) | [可观测性与重放](docs/observability.md) |
| [模型评测框架](docs/model-evaluation.md) | [仓库与证据策略](docs/repository-policy.md) |
| [安全策略](SECURITY.md) | [公开路线图](docs/zh-CN/roadmap.md) |

核心文档提供英语和简体中文语言包。新增语言通过 [`docs/locales.toml`](docs/locales.toml)
独立扩展，不需要运行时翻译服务。

## 参与贡献

欢迎提交 Issue 和范围清晰的 Pull Request。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，
把运行时专属行为放在适配器后，并为可观察行为补充契约测试。

CtxTTL Core 基于 [Apache License 2.0](LICENSE) 开源。
