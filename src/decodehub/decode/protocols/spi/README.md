# SPI 协议模块（`decode.py` / `encode.py`）

> 四线同步总线（主出从入/主入从出 + 片选）。往返测试对见 `tests/property/test_i2c_spi_roundtrip.py`。

## 信号模型

CPOL = 时钟空闲电平；CPHA = 采样发生在前导沿（0）或后导沿（1）。四模式采样沿表：

| 模式 | CPOL | CPHA | 采样沿 |
|---|---|---|---|
| 0 | 0 | 0 | 上升 |
| 1 | 0 | 1 | 下降 |
| 2 | 1 | 0 | 下降 |
| 3 | 1 | 1 | 上升 |

即 `采样沿 = 上升 iff (CPOL==0)==(CPHA==0)`。词长 1–32 位，MSB/LSB 先行；CS 激活期 = 一个 transfer（可含多词）。

## 编码器（encode.py）

内部统一 4 通道位域（CLK/MOSI/MISO/CS）再 `select()` 裁剪（保证位号与通道表一致）：
- CPHA=0：位周期起置 MOSI（半周期建立）→ 前导沿（采样）→ 后导沿回空闲；
- CPHA=1：前导沿时刻同时换数据（移位）→ 后导沿（采样）→ 半周间隙；
- `cs_words=[n]` 生成多词单 CS 突发；`with_miso` 输出 (mosi, miso) 对偶通道。

## 解码器（decode.py）

1. 由 CPOL/CPHA 查表得采样沿类型，取 CLK 对应沿流；
2. CS 给定：从 CS 边沿推活动区间；仅区间内采样沿计入；CS 激活→失活 flush 一个 `spi.transfer`（含有序 words）；**词中 CS 翻转** → `cs-midword` 告警 + 该词复位；
3. CS 缺省：按位计数分词，一次性输出（`no-cs` 告警一次）；
4. 每满 `word_bits` 位输出 `spi.word`（mosi/miso 值按 bit_order 组装）。

## 参数

`clk / mosi / miso / cs`（通道名，miso、cs 可省）、`cpol / cpha`、`word_bits 1–32`、`bit_order`、`cs_active low/high`。

## 事件

`spi.word`（mosi/miso 单词）、`spi.transfer`（CS 区间 + 有序 `words[]`，未接线的缺侧 = None，与 word 事件的 mosi/miso 表示一致）、`spi.warn`（no-cs / cs-midword）。

## 呈现约定（ADR-013，present.py 注册）

- 表格类型名：`spi.word`→`SPI·词`、`spi.transfer`→`SPI·传输`、`spi.warn`→`SPI!`；内容列 = label（transfer 附词数与前 8 词 MOSI HEX 摘要）。
- CSV 专有列：`mosi` / `miso`（HEX）、`word_bits`。
- 时序图 span：参与（`plot_family=true`）；run_decode 摘要 preview：`spi.transfer`、`spi.word`。
