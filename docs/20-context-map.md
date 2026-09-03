# 20 · 限界上下文映射（Context Map）

> 状态：已定案 · DDD 战略设计

## 限界上下文清单

| # | 上下文 | 职责 | 类型 | 代码位置 |
|---|---|---|---|---|
| C1 | **信号内核** Shared Kernel | 统一信号模型（`DigitalWave` / `AnalogChannel` / `Capture`）与领域错误。所有上下文共享的最小公共语言。 | 共享内核 | `decodehub/shared/` |
| C2 | **采集归一化** Acquisition | 格式嗅探 + 各源适配器 → `Capture`；对外的防腐层（ACL）。 | 支撑域 | `decodehub/acquisition/` |
| C3 | **解码** Decode | 图引擎（DAG 规范/验证/求值）+ 节点库（pick/slicer/解码器/过滤器）+ 事件模型。 | 核心域 | `decodehub/decode/` |
| C4 | **呈现** Render | 解码报告 → Markdown 表 / PNG 图表 / 文件导出，制品登记。 | 支撑域 | `decodehub/render/` |
| C5 | **MCP 网关** Gateway（接口层） | 三阶段渐进式工具暴露、会话状态机、门禁、错误翻译。 | 防腐层/表现层 | `decodehub/mcp_server/` |

应用层（`decodehub/app/`）编排 C2–C4 的用例并持有会话状态，不包含领域规则。

## 上下文映射

```
                       ┌───────────────────────────────────────────────┐
                       │                MCP 客户端 (LLM)                │
                       └───────────────────────┬───────────────────────┘
                                               │ MCP (stdio, 渐进式工具)
                       ┌───────────────────────▼───────────────────────┐
                       │  C5 MCP 网关（阶段状态机 · 门禁 · 错误翻译）     │
                       └───────┬───────────────────────────┬───────────┘
                               │ 用例编排                   │ 用例编排
                       ┌───────▼────────┐          ┌───────▼────────┐
                       │ C2 采集归一化    │          │ C4 呈现         │
                       │ 嗅探/适配器(ACL) │          │ 表/图/导出/制品  │
                       └───────┬────────┘          ┌───────▲────────┘
                               │  Capture          │ Events/Report
        ┌──────────────────────▼───────────────────┴──────────────┐
        │              C3 解码（核心域：图引擎 + 节点库）            │
        │      Graph / Node / PortType / DecodedEvent / DecodeReport│
        └──────────────────────┬───────────────────────────────────┘
                               │ 依赖（所有上下文共享）
        ┌──────────────────────▼───────────────────────────────────┐
        │        C1 信号内核（Shared Kernel）                        │
        │        DigitalWave · AnalogChannel · Capture · 领域错误     │
        └───────────────────────────────────────────────────────────┘

上游外部系统（通过文件导出，开放主机服务即各采集器的 MCP/软件）：
  kingstvis MCP ──(csv/kvdat/bin)──▶ C2
  mho98 MCP     ──(csv/npz)───────▶ C2
  MCU 固件      ──(csv/u16bin)─────▶ C2
```

## 上下文间关系（按 DDD 映射模式）

| 上游 → 下游 | 模式 | 契约 |
|---|---|---|
| C1 → C2/C3/C4 | Shared Kernel（共享内核） | `shared/waves.py` 中的 dataclass；变更需三方同步评审 |
| C2 → C3 | 遵奉者（Conformist）+ ACL | C2 输出 `Capture`；外部格式的怪癖（`, ` 分隔符、locale 通道名、缺采样率）止步于适配器内 |
| C3 → C4 | 发布语言（Published Language） | `DecodedEvent` / `DecodeReport` schema 即契约，呈现层对其只读 |
| C5 → C2/C3/C4 | 防腐层 + 用例编排 | 网关不含领域逻辑；工具处理器一行编排一个用例 |
| 采集器生态 → C2 | 开放主机服务（文件格式） | 格式目录见 `40-acquisition.md`；嗅探规则有序可解释 |

## 依赖规则（架构健身测试）

1. `shared` 不依赖任何其他 `decodehub.*` 包。
2. `acquisition` / `decode` / `render` 只依赖 `shared`（decode 不 import acquisition —— 二者通过 `capture` 端口类型在图内交接，而非代码直连）。
3. `app` 与 `mcp_server` 依赖上述全部，但彼此之间 `mcp_server` 只经 `app` 编排（不直接触碰领域对象内部字段，序列化职责在 app/render）。
4. matplotlib 只出现在 `render`；mcp SDK 只出现在 `mcp_server`（与 `app` 的会话状态通过纯 Python 对象交互）。

> 规则 2 的效果：解码上下文可以在没有文件系统的情况下单测（合成波形直接构造 `DigitalWave`）；采集上下文可以在没有图引擎的情况下单测（输出断言在 `Capture` 上）。
