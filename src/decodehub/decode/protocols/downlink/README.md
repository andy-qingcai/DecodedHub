# 下行 DBPSK 协议模块（`decode.py` / `dpsk.py` / `encode.py`）

> 以上行帧网格为锚的槽位包下行（ADR-011）。**跨节点扇入**：`downlink_decode(in: analog, sync: events)`——sync 吃上行解码器的帧起点事件，analog 吃本源原始波形；跨源经 `ProtocolLock.source_inputs` 注入。`dpsk.py` 为 vendored 接收机（零改动）。

## 信号模型（默认档案，全部可配）

263kHz 方波载波 DBPSK；每 bit 10 载波周期；每包 1 起始位（相位参考）+ 16 数据位（640µs 在空）；每上行帧周期 6 个包，槽位偏移为固件标称值（1970/4748/…/15858µs）——**但固件参考点与线上实测差 ~1.8ms，故锚点偏移 delta 由接收机逐包能量扫描自校准，不作为参数**。差分语义：1 = 相对前符号相位翻转 180°。

## 编码器（encode.py）

对每包：起始位提供相位基准，逐数据位按翻转累计半周期偏移 `off`，生成 `sign(sin(2π(fc·t + off)))` 方波；包锚点 = `frame_starts + delta_s + k·slot_period`（delta_s 任意落点即可——接收端自校准容忍整槽位偏差）；`snr_db` 注噪。

## 解码器（dpsk.py，vendored；decode.py 为图封装）

1. **锚自校准**：带通 |y| 能量在整槽位周期上扫描 delta，以"本档案槽位集合跨帧齐亮"为分数（槽集合=档案签名，与包边沿次序无关——多发射机时首个边沿可能属于他人）；
2. **网格重建**：上行帧起点（sync 事件）→ 均匀 60Hz 网格 LSQ 拟合，补全漏帧；
3. **逐包解调**：延迟线鉴相 `z_m = ∫ y(t)·y(t−Tb) dt`（同相→正、翻转→负；**无需载波相位与绝对相位参考**）；载波 fc 逐包 FFT 峰值估计（2^17 补零 ~76Hz 分辨率）+ 双簇共识仲裁（DBPSK 边带 ±1/(2Tb) 会诱导 ±5% 伪载波，以解调度量为最终裁判）；比特栅格锚定包络上升沿 50% 点（自由偏移搜索存在整/半比特歧义）；
4. **槽位角色轮转**：恒载波包（≤1 次翻转）属于对端周期末位——检测恒载波槽并把标签旋转为对端视角（固件偏移参考点在 DSSS 突发前 ~12.9ms，时间序 ≠ 对端槽序）。

## 参数

`channel / profile / fc_nominal / cycles_per_bit / n_bits(含起始位) / slot_offsets_us(列表) / frame_hz / invert`。上/下行 t0 须同触发（>1ms 拒绝——下行锚定要求同一次采集，跨仪器对齐被 ADR-008 v1.2 裁定不可行）。

## 事件

`downlink.packet`（value=16 差分位组装值、value_inv=反相解读、bits、slot、frame、fc_est、confidence）；`downlink.warn`（无上行锚/无包等）。

## 已知语义

恒载波包差分读出可能为全 0 或全 1（极性两极，value/value_inv 双字段保留）；帧编号为网格索引（可与真值整体差一）。

## 测试锚点

`tests/property/test_downlink.py`：跨源/同源双通道往返（逐槽数据位校验）、真实静默 CH2 诚实拒绝、t0 失配拒绝、自定义载波/周期数/包长/槽位。

## 呈现约定（ADR-013，present.py 注册）

- 表格类型名：`downlink.packet`→`下行·包`、`downlink.warn`→`下行!`；内容列 = label 原文（已含槽位/帧号/置信度摘要）。
- CSV 专有列：`value_or_address` = value、`fc_hz` = fc_est、`slot`、`frame`、`confidence`（后三列为并集新列，追加在既有协议列之后）。
- 时序图 span：不参与 timing_plot（模拟直达协议，span 走 analog_plot 的 events 通道，ADR-011）；run_decode 摘要 preview：`downlink.packet`（ADR-013 修复下行摘要不出事件表的缺口）。
