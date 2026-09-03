# ADR-003 · 统一信号模型：数字 = 位域跳变表 IR；模拟 = (t0,dt,n) 紧凑表示

- 状态：已接受（2026-09-03）
- 背景：三类采集源（LA 200MHz / 示波器 / MCU ADC）数据形态迥异；解码与绘图需要统一消费接口。

## 决策

**数字信号**：`DigitalWave` = 多通道位域跳变表——`channels / initial / t_start / edges_t[] / edges_levels[]（每次跳变后的位域快照）/ t_end`。
- 不存逐采样 bool 数组；需要时 `to_bool_array()` 重物化（仅小窗口/测试）。
- 消费接口：`edge_stream(name)`（跳变流）、`level_at(name, t)`（二分）、`select(names)`（子集重掩码）。

**模拟信号**：`AnalogChannel` = `t0/dt/times? + samples(float32) + units + raw_scale?`；均匀采样永不物化 times 数组。

**采集根**：`Capture = meta + digital? + analog[]`，配 `capture_id`（文件名+内容摘要）作为制品目录键。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 逐采样 bool 数组（每通道一个） | 1M 采样×16 通道 = 16MB vs 跳变表 ~10⁴ 条 ≈ 160KB；且固定采样率假设对不同源不成立 |
| 每通道独立跳变流（无位域合并） | 多通道时间对齐（I2C 的 SDA边沿@SCL电平查询）需反复归并；位域快照一次归并终身受益 |
| AnalogWave 强制物化 (t,v) 平行数组 | MHO98 50M 点 = 800MB；紧凑表示 200MB(float32) 且时间零存储 |
| 直接用 xarray/自定义 DataFrame | 引入重依赖；解码器接口反而被绑定到库语义 |

## 依据（实测）

- Kingst 1M 采样：kvdat 250KB / CSV 185KB / bin 2MB——跳变式存储天然是源格式的选择；
- 活跃 UART/I2C 总线 1M 采样典型 10³–10⁴ 跳变：解码 O(E) 毫秒级；
- Kingst 官方 MCP 内部即以"边沿位置+初始电平"存储（调研证实方向正确）。

## 后果

- 正面：数字/模拟统一消费接口；内存与性能达标；跳变语义使"同一解码器服务 200MHz LA 与示波器切片"零特判。
- 负面：`edges_levels` 位域快照要求通道数 ≤ 32（u32）；>32 通道需分多个 DigitalWave（Kingst/Saleae 均 ≤16，余量充足）。
- 模拟→数字转换（阈值切片）产生新 DigitalWave，阈值写回 `meta.threshold_v` 保证可追溯。
