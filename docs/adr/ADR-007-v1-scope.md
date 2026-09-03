# ADR-007 · v1 适配器与协议范围（及明确延后项）

- 状态：已接受（2026-09-03）

## 决策：v1 实现范围

**采集适配器（9 个嗅探路径）**：`kingst_csv` / `kingst_bin` / `kingst_kvdat` / `mho98_csv` / `mho98_npz` / `mcu_adc_csv` / `mcu_adc_bin` / `saleae_csv`（数字）/ `generic_csv`（模拟兜底）。

**协议解码器（3 个）**：`uart_decode`（自动波特率、5–9 数据位、奇偶、1/1.5/2 停止、反相、MSB/LSB）、`i2c_decode`（7/10-bit、重复起始、时钟拉伸容忍、bus-free 告警）、`spi_decode`（四模式、CS 帧化、1–32 位词、MSB/LSB、缺 CS 降级）。

**节点**：`digital_pick` / `analog_pick` / `slicer` / `event_filter` + 上述三个解码器。

## 明确延后（架构已预留）

| 项 | 现状 | 将来路径 |
|---|---|---|
| Saleae `.sal` / 数字 bin v0/v1 / 模拟 bin | 嗅探命中即报"规划中"（诚实错误） | 二进制规格已备档（调研报告）；新增 `adapters/saleae_bin.py` 即可 |
| CAN / 1-Wire / RS232 高层帧 / 自定义校验和 | — | 按 `30-architecture.md` 扩展指南：Node 子类 + 注册 + 模板行 + 往返测试 |
| 甘特图 / 总线统计图 | — | `render/plots.py` 追加视图；事件 schema 已含全部所需字段 |
| `baud_measure` 标量节点 | UART 解码器内置 auto-baud | 需要独立测量时按 PARAMS 契约加入 |
| 流式/长采集分窗 | 单窗全量（50M 模拟点可行） | 跳变 IR 天然支持窗口化；`event_filter` 已有时间窗参数 |
| 多会话 HTTP 部署 | stdio 单会话/进程 | 状态以会话对象为键，语义上已就绪；注意低层 Server 的工具缓存是进程级的，届时需每连接一 Server 实例 |

## 依据

- "先做简单的解码协议进行测试"——UART/I2C/SPI 覆盖 95% 嵌入式调试场景且算法成熟（sigrok 交叉验证）；
- 适配器集合以本机真实样本可验证为界（kingstvis/rigol data/ 全覆盖）；Saleae 二进制格式因无本机样本对照，防错解析毒化数据而延后；
- 渲染延后项以"事件 schema 已含所需字段"为前提，确保加视图不动领域层。
