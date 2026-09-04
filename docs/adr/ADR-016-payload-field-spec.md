# ADR-016 · payload 字段规格：一种语言描述、同一套算法结算（decode/fields.py + field_split 节点）

- 状态：已接受（2026-09-04）
- 背景：总线级解码（信号→帧）已由各协议节点解决，但**帧 payload 的小字段切分**
  此前没有机制——`I2cEvent.data_bytes` / `UplinkEvent.data_bits` 等原始载荷虽已
  结构化保留在事件上，字段语义全靠各协议 `present.py` 的 detail_fn 手写格式化。
  调研结论（2026-09-04）：报文的分段、类别分发、动态长度是协议描述语言（PDL）
  的核心原语，Kaitai Struct / construct / Spicy 等均以"一种声明式规格 + 一个
  递归下降解释器"覆盖，约九成二进制格式可纯声明式描述。

## 决策

1. 新建 `decode/fields.py`：六原语字段规格语言 + 唯一解释器，规格为**纯 dict**
   （可经 JSON/MCP 下发，语法精神同 Kaitai Struct 子集）：
   ①序列（`seq`，u8..u64/s/f/b 位域/bytes/str/自定义类型）②重复（`repeat`:
   expr/eos/until）③动态长度（`size` 表达式 / `size_eos` / `terminator`）
   ④类别分发（`switch_on` + `cases`，`"*"` 缺省）⑤计算与校验（`value` 表达式、
   `valid:{eq}`、`contents`）⑥辅助（`enum` / `if` / `endian`（缺省 be）/
   `root.`/`parent.` 跨层引用 / `len(x)`）。表达式为 AST 白名单子集，编译期
   拒绝一切调用/下标/越界名字。
2. 同一套算法：`compile_spec`（fail fast，注册/建图即暴露规格错误 →
   `FieldSpecError`）+ `parse_payload`（递归下降 + 部分解析树表达式求值）。
   解析期数据错误是 `FieldView.errors`（`truncated`/`valid`/`no-case`/`expr`），
   不中断其余字段——ADR-004"错误是数据"同款哲学。
3. 新节点 `field_split`（events→events）：原帧事件**保留**，`FieldSetEvent`
   （kind `fields.split`，含字段树）紧随其源插入（全局时间有序不变量保持）。
   规格来源二选一：内联 `spec`（优先）或具名 `spec_name`（`register_fields`
   注册表）；载荷提取按协议族分派（i2c/uart/spi/uplink/downlink 内建，
   缺省按事件 kind 前缀自动，`register_payload_extractor` 覆盖式扩展）。
4. 边界：PDL 只管"帧 payload → 字段树"。总线级解码仍是图节点职责（时间域问题，
   规格语言无表达力）；位级动态长度与 CRC 等聚合校验是已知边界（与 Kaitai 同款
   弱项），留作逃生舱（自定义 value 处理）而非内建。
5. 呈现：`fields` 呈现约定由 `decode/__init__` 在**协议族之后**注册（注册函数
   `register_fields_presentation`），遵守 ADR-013 CSV 并集列序契约——既有协议列
   序不变，`fields/source_kind/spec` 三列追加在尾；`plot_family=False` 不污染
   时序图。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 各协议 decode.py 内继续手写字段解析 | 每协议一份算法，恰是本次要消除的形态；动态长度/分发逻辑重复 |
| 直接依赖 construct（Python 解析+构建对称） | 规格即 Python 代码，无法经 MCP/JSON 下发、不可序列化进 Profile；引入运行期 exec 面更大 |
| 兼容完整 Kaitai .ksy 作为输入 | 编译器/运行时依赖重；先落六原语子集，语法精神一致，将来需要时做 .ksy 导入是增量 |
| 规格挂在 `ProtocolBinding.fields`（ADR-014） | 方向正确但 binding 刚落定（a15bbf8），避免耦合其演进；节点层先行，迁移到协议侧五件套是纯增量 |

## 后果

- 正面：payload 字段从"手写格式化"变为"声明规格"；同一份规格可反哺 encode
  （合成带语义的帧）；规格可经 MCP 工具参数下发（用户自定义协议不必改代码）；
  `FieldView` 可 asdict 序列化（JSON 导出 bytes 转 hex）。
- 负面/边界：`spec` 参数走 `Param("any")`，规格错误在运行期（NodeError 包装）
  而非建图期暴露；位载荷（uplink/downlink）按"首 bit=首字节 MSB、尾部补零"
  转字节后再切分，位偏移字段规格暂不支持；具名规格的分发进协议包
  （`protocols/<p>/fields.py` 五件套）待 ADR-014 稳定后另行迁移。
- 测试：`tests/unit/test_fields.py`（六原语语义 + 编译期拒绝）、
  `tests/unit/test_field_split.py`（节点契约/插入序/呈现）、
  `tests/property/test_payload_fields.py`（synth I2C → 真实图端到端）。

## 约束（注册纪律）

全部注册表（NODE_REGISTRY / _FIELDS / _FIELD_FNS / _CHECK_FNS /
_PAYLOAD_EXTRACTORS / _PRESENTATIONS）**只允许在 import 期写入**——
MCP 工具跑在 worker 线程（anyio.to_thread），注册发生在请求路径会把
模块级 dict 变成并发写入面。协议包 = import 一次、进程生命周期生效；
运行期改变解析行为请重建图/管线（ADR-020 管线不接受 overrides 同理）。

## 补充（2026-09-04 评审反馈：校验必须能落地，代码怎么办）

- 反馈：纯配置覆盖不了 CRC 校验这类能力，"嵌入它们必然带上代码"。
- 结论修正：**CRC 恰恰可以不带代码**——它是参数化算法族（width/poly/
  init/refin/refout/xorout，Rocksoft™ 模型）。落地分两层，规格保持纯数据：
  1. **校验算法目录（数据层）**：内建位级参数化实现 + 预设（crc8 /
     crc16_ccitt_false / crc16_xmodem / crc16_modbus / crc32 / sum8 / xor8）。
     字段级 `"crc": {"algo": "crc16_modbus"}`（over 缺省 "prefix" =
     本层结构起点到本字段之前，嵌套结构只覆盖自己）；非标模型直接给内联
     参数。不符记 `crc` 错误（错误词表随 ADR-004 哲学扩展）。
  2. **具名代码钩子（逃生舱）**：真正写不成表达式的变换（私有扰码/解密/
     自定义打包）与自定义校验算法，经 `register_field_fn`（process 变换）/`
     register_check_fn`（`"crc": {"fn": 名字}`，(bytes)->int）注册在**受信侧**
     （协议包内、随代码版本走、走评审），规格只引用名字：
     `"process": "pen.scramble"`、`"crc": {"fn": "pen.crc_custom"}`。
     编译期校验名字已注册。错误词表相应含 `incomplete`（子结构截断冒泡）
     与 `no-progress`（重复零推进防死循环守卫）。
- 否决的备选：规格内嵌代码片段（eval/exec）——不可静态校验、不可审计，
  MCP 下发即任意代码执行；construct 式"裸 callable 进规格对象"只适合
  进程内使用，破坏规格可序列化/可下发性。
- 边界：v1 的 crc.over 仅 "prefix"（覆盖 90% 的尾部校验场景）；CRC 跨
  任意字节区间的显式 from/to 表达留待真实需求出现再加。

## 补充二（2026-09-05 评审反馈：输出要给人看）

- 反馈：`bLength=0x12 bDescriptorType=0x01(DEVICE) …` 一行流是调试视图，
  不是给人看的报表——十进制量出十六进制、BCD 版本号不翻译、单位丢失、
  嵌套结构塞一行。
- 决策：**呈现知识声明在规格里**（与解析知识同源、同处声明）：字段级
  `display: hex|dec|bcd`（bcd → "2.00" 版本号）、`scale`（显示值 = value×scale）、
  `unit`（"mA" 等单位后缀）。提示随 FieldView 携带，**value 永远是原始值**
  ——机器通道（JSON/程序处理）不变。Markdown 内容列从一行流改为 `<br>`
  换行缩进的字段树（struct 展开为组标题行、`└` 缩进）；枚举直接出名字；
  全可打印的 bytes 附 ASCII。label/CSV 保持单行紧凑（预览与 Excel 过滤场景）。
- USB 样例同步更新：bcdUSB=2.00、bMaxPower=100 mA（原 max_ma 计算字段由
  scale/unit 取代）、长度/索引出十进制。
