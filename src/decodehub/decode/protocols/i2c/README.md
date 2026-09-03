# I2C 协议模块（`decode.py` / `encode.py`）

> 两线开漏同步总线。SCL/SDA 同在一张 DigitalWave 上（2 通道位域），往返测试对见 `tests/property/test_i2c_spi_roundtrip.py`。

## 信号模型

开漏 + 上拉：线闲高。START = SCL 高时 SDA 下降；STOP = SCL 高时 SDA 上升；重复 START（Sr）= 开放传输中的 START。数据只在 SCL 低期变化，SCL 上升沿采样。每字节 8 位 MSB 先行 + 第 9 位 ACK 槽（0=ACK）。7-bit 地址 = 首字节高 7 位 + R/W；10-bit 由首字节 `11110xx` 宣告、次字节补全。时钟拉伸 = 从机拉低 SCL。

## 编码器（encode.py）

按"半周期栅格"逐位生成快照序列 `(t, SCL|SDA 位域)`：
- 每数据位：低半周置 SDA（数据在 SCL 低期建立）→ 高半周（上升沿处 SDA 已稳定）；
- ACK 槽同理，按事务的 acks 列表驱动（`final_nack` 便捷置末位 NACK）；
- **STOP 时序**：SCL 低半周拉 SDA 低 → SCL 高 → SDA 升（SCL 高期变化才是 STOP——不能 dt=0 拼时刻，物理不可能且会触发跳变表的同时刻归并）；
- **Sr 时序**：SCL 低释放 SDA 高 → SCL 高 → SDA 降；
- `stretch_s`：首个位前 SCL 低期延长（时钟拉伸注入）。

## 解码器（decode.py）

事件驱动，位采样只发生在**实际 SCL 上升沿** ⇒ 时钟拉伸天然免疫（ADR-005）：

1. 归并 SDA 边沿与 SCL 上升沿（同时刻 SDA 判定先行）；SDA 边沿查 `level_at(SCL, t)`：SCL 高 → START/Sr/STOP 判定；SCL 低 → 正常数据变化，忽略。
2. SCL 上升沿：`bitcount<8` 移位；`==8` 为 ACK 槽 → 字节完成。地址期解析 7/10-bit；数据期追加字节。拉伸告警：SCL 低电平持续 > `stretch_warn_s` → warn 事件（不影响解码）。bus-free 违例（STOP→START < 1.3µs）→ warn。
3. 输出双层事件：细粒度（start/Sr/stop/addr/data，供绘图标注）+ **传输级** `i2c.transfer`（START..STOP 汇总：地址、方向、数据、逐字节 ACK——取最后一次 START 的方向，符合"写寄存器后读出"的组合事务语义）。
4. 截断（STOP 缺失至采集尾）→ transfer 带 `truncated`。

## 参数

`scl` / `sda`（通道名）、`stretch_warn_s`（默认 1ms）。

## 事件

`i2c.start / repeat-start / stop / addr / data / transfer / warn`；transfer 含 `address / is_10bit / read / data_bytes / acks[]`。NACK 语义只承载于 transfer 的逐字节 `acks[]`（data 事件以 ann_class=warn 呈现，`errors` 恒空）。

## 呈现约定（ADR-013，present.py 注册）

- 表格类型名：`i2c.start`→`I2C·S`、`i2c.repeat-start`→`I2C·Sr`、`i2c.stop`→`I2C·P`、`i2c.addr`→`I2C·地址`、`i2c.data`→`I2C·数据`、`i2c.transfer`→`I2C·传输`、`i2c.warn`→`I2C!`；内容列 = label（transfer 有数据时附 ASCII 摘要，≤16 字节）。
- CSV 专有列：`value_or_address` = address、`read`、`data_bytes`（HEX 串）、`acks`（A/N 串）。
- 时序图 span：参与（`plot_family=true`）；run_decode 摘要 preview：`i2c.transfer`、`i2c.addr`。
