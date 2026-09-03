# 00 · 愿景与范围（Vision）

> 状态：已定案（v1 基线） · 日期：2026-09-03

## 一句话愿景

把逻辑分析仪、示波器、MCU ADC 三类采集设备导出的异构数据**归一化**为统一信号模型，通过**图（DAG）节点流水线**完成通信协议解码，产出结构化数据与可视化图表，并以**渐进式暴露**的 MCP 接口服务 LLM 客户端——初始只给少量工具，锁定数据源与协议后才解锁完整操作集，最大限度节省上下文。协议以"一协议一目录"组织（编码+解码+原理文档三件套，ADR-012）。

## 要解决的问题

| 痛点 | 本平台的回答 |
|---|---|
| 每种采集器导出格式都不同（Kingst CSV/kvdat/bin、RIGOL CSV/NPZ、MCU 串口裸数据…），分析脚本一次性、不可复用 | 采集上下文 + 格式嗅探 + 统一信号模型（一次适配，处处可用） |
| 协议解析逻辑复杂、多变（阈值切片 → 边沿提取 → 位级解码 → 帧重组 → 呈现），函数式写法很快失控 | 显式 DAG 图引擎：节点有类型化端口，可验证、可缓存、可局部重算、可被 LLM 检视（`inspect_graph`） |
| MCP server 一次暴露几十个工具，LLM 上下文被工具 schema 淹没 | 三阶段状态机（DISCOVERY → SOURCE_LOCKED → READY），锁定前只暴露 6 个工具 |
| 解码结果难读：hex dump / 事件表 / 波形图彼此脱节 | 呈现上下文：图文配对（图内编号 span ↔ Markdown 表）+ 结构化 JSON/CSV 导出 |

## 目标（v1）

1. **归一化**：支持 Kingst（CSV / bin / kvdat）、RIGOL MHO98（norm CSV / raw NPZ）、mcu_adc（CSV 变体 / u16 LE bin）、Saleae 数字 CSV、通用模拟 CSV；自动嗅探格式。
2. **解码**：UART（自动波特率）、I2C（含 10-bit 地址、重复起始、时钟拉伸）、SPI（四模式、CS 帧化）；模拟信号经滞回阈值切片后同样可解码。
3. **呈现**：多通道数字时序图（含解码帧着色与编号标注）、模拟波形 + 阈值叠加图；Markdown 事件表；JSON/CSV 导出。
4. **MCP 渐进式暴露**：低层 `mcp.server.Server` + 每会话 `list_tools` 过滤 + `tools/list_changed` 通知（已对 mcp 1.29.1 端到端实测）。
5. **可测试**：合成波形发生器 → 解码器的往返（round-trip）属性测试 + 真实样本冒烟。

## 非目标（v1 明确不做）

- 直接驱动采集硬件（采集由既有 MCP：kingstvis、mho98 完成；本平台消费其导出文件）。
- Saleae `.sal` / 二进制 v1、CAN / 1-Wire / USB 等更多协议（架构已预留，见各上下文文档"扩展指南"）。
- 实时流式解码（面向离线采集文件；跳变 IR 使未来流式化容易）。
- Web UI / 交互式图表（静态 PNG 足够 LLM 场景；见 ADR-006）。

## 成功标准

1. 对 `examples/` 中合成 UART/I2C/SPI 采集，`lock_source → lock_protocol → run_decode → render_timing` 全链路一次走通，往返字节零误差。
2. 对本机真实导出（kingstvis `data/`、rigol `data/`）能正确嗅探、归一化、出图。
3. MCP 冒烟测试（in-memory client）验证：初始仅 4 工具；`lock_source` 后客户端收到 `tools/list_changed` 并看到阶段二工具集。
4. 新增一个协议解码器只需：实现 `Node` 子类 + 注册 + 测试（不改引擎、不改网关）。

## 生态位

```
┌─────────────┐   导出文件    ┌──────────────────────────────────┐
│ kingstvis MCP│ ─────────▶ │                                  │
│ (Kingst LA)  │  csv/kvdat/ │        decoded-all-in-one        │
├─────────────┤  bin        │  (本项目: decodehub)              │
│ mho98 MCP    │ ─────────▶ │  归一化 → DAG 解码 → 呈现         │
│ (RIGOL 示波器)│  csv/npz   │                                  │
├─────────────┤            │        ▲ MCP (渐进式暴露)          │
│ MCU 固件     │ ─────────▶ │        │                          │
│ (mcu_adc)    │  csv/bin   │        │                          │
└─────────────┘            └────────┼──────────────────────────┘
                                    │
                              LLM 客户端（ZCode / Claude …）
```
