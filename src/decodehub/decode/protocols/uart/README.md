# UART 协议模块（`decode.py` / `encode.py`）

> 单线异步串口。解码吃**数字跳变流**（DigitalWave），编码产出同一表示——两者构成往返测试对（`tests/property/test_uart_roundtrip.py`）。

## 信号模型

空闲高电平；帧 = 起始位(0) + 5–9 数据位（LSB 先行，可配 MSB）+ 可选奇偶位 + 1/1.5/2 停止位(1)。背靠背帧间可零空闲。

## 编码器（encode.py）

逐帧把起始/数据/校验/停止位展开为电平段，仅记录**变化沿**（`(t, 新电平)`）→ `DigitalWave.from_segments` 压缩为跳变表：
- `idle_bits` 控制帧间空闲（含 0 = 背靠背场景）；
- `jitter_ui`：每条边 ±0.25×UI 均匀抖动（`maximum.accumulate` 保单调）；
- `drift_ppm`：每帧位周期按发端钟差缩放；
- `invert`：输出物理反相波形；
- 奇偶位按 O/E 期望计算，供解码端校验错误注入（翻转校验参数即得 parity 错误样本）。

## 解码器（decode.py）

跳变驱动（ADR-005），不重采样：

1. **自动波特率**（`baud="auto"`）：收集全部低脉冲宽度 W，`bit_T = median({w<1.5·min(W)})`——空闲高线上起始位是唯一保证的单 bit 低脉冲；候选离散度 >10% 置 `baud-uncertain` 告警。
2. **帧循环**：对每个候选下降沿 ts（跳过 `t < 上一帧尾`）：
   - BREAK：低电平持续 ≥ 整帧 → `break` 事件，越过该低电平段重新找起始；
   - 起始位校验：`level(ts + bit_T/2) == 0`，否则 `spurious-start` 告警事件，取下一沿；
   - **锚定采样**：以起始位中点为锚，各位算术推进 `+bit_T` 采样（等值位连发无沿可同步；≤12 bit 收发钟差 ≪ 半 bit，逐帧重锚即够）；
   - 奇偶校验按 popcount 判定；停止位采样于**停止位中点**（+0.5，n>1 时再加 +n−0.5）——采样末端会与下一帧起始位重叠，是背靠背误判 framing 的根源；
   - 采样点越过 `t_end` → `truncated`（冻结外推语义见 shared/waves）。
3. 错误是事件字段（ADR-004）：`parity / framing / break / truncated / spurious-start`；解码永不中断，恢复点 = 下一下降沿。

## 参数

`rx`（通道名）、`baud`（数值/auto）、`data_bits 5–9`、`parity N/O/E`、`stop_bits 1/1.5/2`、`invert`、`bit_order lsb/msb`。

## 事件

`uart.frame`：`value / data_bits / parity / errors[]`；`t_start` = 起始沿，`t_end` = ts + 帧总位长×bit_T。

## 呈现约定（ADR-013，present.py 注册）

- 表格类型名：`uart.frame`→`UART`、`uart.warn`→`UART!`；内容列 = label（8bit 且无错误的帧附 ASCII：`8N1 'A'`）。
- CSV 专有列：`value_or_address` = value。
- 时序图 span：参与（`plot_family=true`）；run_decode 摘要 preview：`uart.frame`。
