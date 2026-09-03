# 上行 DSSS 协议模块（`decode.py` / `dsss.py` / `encode.py`）

> 60Hz 突发帧的直接序列扩频上行。**模拟直达**（apick → uplink_precond → uplink_decode，不经 slicer——解扩需要幅度软信息，ADR-010）。`dsss.py` 为 vendored 接收机（出处 `原vendored工程`，DSP 参数实机调参，零改动）。

## 信号模型（默认档案，全部可配——ADR-011）

每 60Hz 周期（16.67ms）一个 ~248µs 突发帧（占空比 1.5%）；帧 = 前导 001 + 5 数据 bit = 8 符号；每符号 = 完整 31-chip m 序列（0x3DA60E45，x^5+x^2）双极性 NRZ（1→+A / 0→−A）；码片标称 1µs，实测 0.9692µs（−3%，接收机自动估计）。信号骑在强 60Hz 包络上。

## 编码器（encode.py）

比特流 → 按符号选 PN 原码/反码 → 展开为 chip 电平 → 按 `ppm` 缩放的位周期采样为 NRZ 方波：
- `period_s=None` 连续模式 / `period_s=16.67e-3` 突发模式（帧间静默，捕获含尾部静默——真实信道形态）；
- 注入轴：`ppm`（发端钟差，真实信道 ≈ −30800）、`snr_db`、`env_amp/env_hz`（60Hz 包络叠加）、`dc`、`amp`（负值即反相——用于验证极性自适应）；
- 协议形状覆写 `pn_word/pn_len/pream/data_bits_n/chip_s/unipolar` 与解码参数一一对应。

## 解码器（dsss.py，vendored；decode.py 为图封装）

链路：预条件（抽取 ~12 样点/chip + 1ms 零相位滑动均值 HPF 剥包络——**原始波形上匹配滤波会锁住包络，必须先剥离**）→ 全段 FFT 相关（对一个 PN 周期）→ 梳齿提取符号（全局极大锚定 + 抛物线 + LSQ 亚样本精化；锚定式而非自由峰拾取，规避芯片级相关纹路抖动）→ 双模自适应能量分段（连续 0.5×p99 / 突发 median+10×MAD 含 p99 15% 下限）→ 突发内 8 相位 × 2 极性软评分前导同步（数据位 = 峰值符号；极性搜索 ⇒ **接收机极性自适应**）→ 码片速率候选仲裁（梳齿扫描 top-k，前导通过率 ≥50% 才接受——真实信道干扰结构可能在单窗口上得分超过真周期，前导检查是最终裁判）。

**诚实拒绝**：纯噪声/无突发 → 无帧 + warn 事件，绝不输出伪流量。

## 参数与物理约束

节点参数：`channel / profile / chip_s / pn_word(hexint) / pn_len / pream(位串) / data_bits / invert / unipolar / msb_first`。
约束：PN 字必须具 m 序列级近零旁瓣自相关（7-chip 随机字实测不可解）；默认策略按 31-chip 调参，**改 PN 长度需配套新档案**（`UPLINK_PROFILES` 登记形状+策略整体）；已验证可配轴：同长度换 m 序列字、数据位数、码片速率、极性、映射。

## 事件

`uplink.frame`（value/data_bits/pream_ok/confidence/burst；t_start = 帧首符号解扩峰，含采集 t0 归一）；`uplink.warn`（码片偏差/拒绝原因等接收机诊断）。

## 测试锚点

`tests/property/test_uplink_roundtrip.py`：往返、突发+包络+SNR 8dB、−30800ppm、极性自适应、噪声拒绝、真实 `uplink24ms_ch1.npz` 黄金（0x01，与原项目 real_out.json 一致）。

## 呈现约定（ADR-013，present.py 注册）

- 表格类型名：`uplink.frame`→`上行·帧`、`uplink.warn`→`上行!`；内容列 = label + `bits=` 数据位串。
- CSV 专有列：`value_or_address` = value、`pream_ok`、`confidence`。
- 时序图 span：不参与 timing_plot（模拟直达协议，span 走 analog_plot 的 events 通道，ADR-010）；run_decode 摘要 preview：`uplink.frame`。
