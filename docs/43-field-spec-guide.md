# 43 · 字段规格编写指南（payload field spec）

面向**规格作者**：怎么用一份 JSON 描述一种报文格式，让同一套引擎
（`decode/fields.py`，ADR-016）结算出全部小字段。规格是纯数据——可经
MCP 下发、可入工程档案、可版本管理；解析/校验/呈现三层知识都声明在
规格里。完整参考实现：`examples/usb_device_descriptor.json`（定长）、
`examples/usb_configuration_descriptor.json`（嵌套分发）、
`examples/packs/sensorx/`（自定义校验钩子的协议包）。

## 0. 心智模型

- 规格是**声明**，不是程序。写错在编译期报 `FieldSpecError`（fail fast）；
  数据不符在字段上记 `errors`（`truncated`/`valid`/`crc`/`no-case`/`expr`），
  不中断其余字段（ADR-004 哲学）。
- 字段树里 `value` **永远是原始值**（机器通道）；"人怎么看"由呈现提示
  （`display`/`scale`/`unit`/`enum`）声明，渲染层负责翻译（人通道）。
- 能用数据表达的不写代码：CRC 预设 > 内联参数 > 具名函数钩子。

## 1. 最小规格

```json
{
  "endian": "be",
  "seq": [
    { "id": "cmd",  "type": "u8",  "enum": { "1": "READ", "2": "WRITE" } },
    { "id": "len",  "type": "u8" },
    { "id": "body", "type": "bytes", "size": "len" }
  ]
}
```

```python
from decodehub.decode.fields import parse_payload
parse_payload(spec, bytes.fromhex("0102ABCD"))
# [cmd=READ, len=2, body=b'\xab\xcd']
```

## 2. 顶层键

| 键 | 类型 | 语义 |
|---|---|---|
| `seq` | 字段数组（必填） | 顶层序列 |
| `types` | 名 → `{seq:[…]}` | 自定义类型表，供 `type` 名与 `cases` 引用 |
| `endian` | `"be"`/`"le"` | 顶层缺省字节序（缺省 **be**，仪器/网络序）；字段可覆盖 |
| `doc` | 字符串 | 规格说明（不入引擎逻辑） |

## 3. 字段类型

| type | 宽度 | 说明 |
|---|---|---|
| `u8 u16 u24 u32 u64` | 8..64 bit | 无符号整数 |
| `s8 s16 s24 s32 s64` | 8..64 bit | 有符号（补码） |
| `f32 f64` | 32/64 bit | IEEE 浮点 |
| `b1`..`b64` | 1..64 bit | 位域：**MSB 在前**，连续位域共享位游标，遇到非位域字段对齐到字节边界 |
| `bytes` | 动态 | 字节串，需要 `size`/`size_eos`/`terminator`/`contents` 之一 |
| `str` | 动态 | 同 bytes + `encoding`（缺省 ascii），读出为 str |
| `"名字"` | — | `types` 表中的自定义类型（struct） |

## 4. 六原语

### 4.1 序列字段

```json
{ "id": "temp", "type": "s16", "endian": "le", "doc": "温度 ×0.1℃" }
```

### 4.2 重复（`repeat`）

| 形式 | 写法 | 语义 |
|---|---|---|
| 计数 | `"repeat": "expr", "repeat_expr": "n"` | 重复 n 次（n 是表达式） |
| 到流尾 | `"repeat": "eos"` | 重复到载荷结束（不足一个最小项即停） |
| 到哨兵 | `"repeat": "until", "until": "t == 0xFF"` | **含满足条件的末项** |

重复项可以是标量、bytes/str、自定义结构（含 switch）。发生截断的重复
组记 `incomplete`，其后的序列字段不再解析。

### 4.3 动态长度

| 写法 | 语义 |
|---|---|
| `"size": "n"` | 字节数 = 表达式值（**单位是字节**） |
| `"size": "n * 4 - 1"` | 任意算术 |
| `"size_eos": true` | 读到整个载荷尾（嵌套结构内慎用——会吞掉后续字段） |
| `"terminator": 0` | 读到哨兵字节为止，**哨兵不含在值里**（游标越过它） |
| `"contents": "A5 5A"` | 内容断言，隐含定长；不符记 `valid` |

### 4.4 类别分发

```json
{ "id": "body", "switch_on": "cmd",
  "cases": { "1": "read_body", "2": "write_body", "*": "generic" } }
```

- 判别字段（`cmd`）必须**先于** switch 字段声明；
- `cases` 的键是字符串（JSON），支持 `"0x10"` 十六进制；`"*"` 为缺省分支，
  无匹配且无 `*` 时记 `no-case`（游标不动，其余字段继续）。

### 4.5 计算与校验

```json
{ "id": "temp_c",   "value": "t_raw / 10.0" },
{ "id": "ver",      "type": "b4", "valid": { "eq": "2" } },
{ "id": "magic",    "type": "bytes", "contents": "5453" },
{ "id": "crc",      "type": "u16", "endian": "le", "crc": { "algo": "crc16_modbus" } }
```

- `value`：计算字段，**不消耗字节**，不参与重复/条件；
- `valid.eq`：字段值 == 表达式值（隐式相等），不符记 `valid`；**仅标量**
  （bytes/str 用 `contents`，编译期拒绝）；
- 移位表达式上限 4096（巨整数防护）；`and`/`or` 短路求值；
- `crc` 三种写法，优先级从数据到代码：
  1. **预设**：`{"algo": 名字}` —— `crc8`(0xF4) `crc16_ccitt_false`(0x29B1)
     `crc16_xmodem`(0x31C3) `crc16_modbus`(0x4B37) `crc32`(0xCBF43926)
     `sum8` `xor8`（括号内为标准校验值 `"123456789"` 的已验证结果）；
  2. **内联参数**（非标模型）：`{"width": 16, "poly": 0x8005, "init": 0xFFFF,
     "refin": true, "refout": true, "xorout": 0}`（Rocksoft™ 模型）；
  3. **具名函数**：`{"fn": "包名.函数"}` —— 引擎预设覆盖不了的算法，
     代码注册在受信侧（见 §8），签名 `(bytes) -> int`，返回值须与字段位宽一致；
- 覆盖范围 `over` v1 仅 `"prefix"`（缺省）：**本层结构起点 → 本字段之前**；
  嵌套结构的 CRC 只覆盖自己，不含外层帧头。存储字节序由字段 `endian` 决定
  （Modbus 惯例 `"le"`）。不符记 `crc`。

### 4.6 辅助（呈现提示与钩子）

| 键 | 适用 | 语义 |
|---|---|---|
| `enum` | 整数标量 | `{"1": "IDLE"}`，键支持 `"0x10"`；命中显示名字 |
| `if` | 消耗字段的字段 | 表达式真才解析（如 `"alarm_high | alarm_low"`） |
| `display` | 标量 | `hex`（缺省）/`dec`/`bcd`（0x0210 → "2.10"） |
| `scale` | 标量 | 显示值 = value × scale（value 不变） |
| `unit` | 标量 | 单位后缀（`"mA"`），配合 scale：`bMaxPower=100 mA` |
| `process` | bytes/str | **具名变换钩子**：读出后先变换再 contents/解码 |
| `encoding` | str | 缺省 ascii |
| `doc` | 全部 | 说明文案 |

## 5. 表达式语言

- **允许**：`+ - * / % << >> & | ^`、比较 `== != < <= > >=`、`and or`、
  一元 `~ - +`、`len(x)`、`root.xxx` / `parent.xxx` 跨层引用。
- **拒绝**（编译期报错）：一切函数调用（`len` 除外）、下标、任意属性、
  未声明名字——规格不可携带可执行语义。
- 名字解析：本层已声明的字段 id → 外层逐级；`root.` 最外层、`parent.`
  上一层。自定义类型编译期可见的名字 = 自己的 seq + 顶层 seq 的 id；
  引用**兄弟结构的字段**用 `parent.`（编译器豁免属性链的静态检查）。
- `/` 是真除法（物理量缩放 `t_raw / 10.0`），`//` 是整除。

## 6. 错误词表（FieldView.errors）

| 标记 | 含义 |
|---|---|
| `truncated` | 读越界（载荷不够） |
| `valid` | `valid.eq` 或 `contents` 断言不符 |
| `crc` | CRC/校验和/具名校验不符 |
| `no-case` | switch 无匹配分支且无 `*` |
| `expr:…` | 表达式求值失败（未知名字/除零/类型不符） |
| `incomplete` | 子结构内发生截断（向上冒泡，后续字段停解析） |
| `no-progress` | 重复项零字节推进（防死循环守卫终止，如 eos × 无匹配 switch） |

## 7. 呈现：三个通道

- **Markdown 内容列**：换行缩进的字段树（struct 展开为组标题、`└` 缩进），
  按提示出人话——`bcdUSB=2.00`、`bMaxPower=100 mA`、枚举出名字、
  全可打印 bytes 附 `'TS'`；
- **label/CSV**：单行紧凑（MCP 预览、Excel 过滤场景）；
- **JSON**：`value` 恒为原始值（机器比对用），提示/枚举名/errors 一并带出。

## 8. 多种报文格式 + 自定义校验代码：解耦结构

三条原则：

1. **规格是数据，代码不是规格。** 规格可下发/可入档案/可审计；
   代码永不内嵌进规格（拒绝 eval/exec——MCP 下发即任意代码执行）。
2. **两者靠名字绑定，编译期校验。** 规格里写 `"crc": {"fn": "sensorx.sum_xor"}`，
   函数通过 `register_check_fn` 注册在受信侧；名字不存在，编译即报错。
3. **一种报文格式 = 一个协议包**，声明与代码同包同居、同版本走。

### 包布局（单一仓库内的推荐结构）

```
examples/packs/sensorx/            ← 用户协议包（参考实现，照抄这个形状）
├── __init__.py                    # 只做注册（导入侧效应），零解析逻辑
├── spec_frame.json                # 纯声明
├── checks.py                      # 校验/变换纯函数——代码唯一住处
└── README.md                      # 协议原理（帧图/来源）
```

`__init__.py` 注册模板：

```python
import json
from pathlib import Path
from decodehub.decode.fields import register_check_fn, register_fields
from . import checks

register_check_fn("sensorx.sum_xor", checks.sum_xor)
register_fields("sensorx.frame",
                json.loads((Path(__file__).parent / "spec_frame.json").read_text()))
```

### 决策规则

| 场景 | 去处 |
|---|---|
| CRC 预设/参数能表达 | 规格 `crc.algo`/内联参数（**不写代码**） |
| 引擎预设覆盖不了的校验算法 | 包内 `checks.py` 纯函数 + `register_check_fn` |
| 载荷的变换/解扰/解码 | 包内纯函数 + `register_field_fn`（规格 `process` 引用） |
| 协议进 decodehub 核心 | `decode/protocols/<p>/` 五件套（ADR-012：decode/encode/binding/README + fields），随核心测试与发布 |
| 自有报文格式（不进核心） | 独立协议包（如上），规格与代码同仓不同层 |

### 命名空间与接线

- 一切注册名用 **`<包名>.<名称>`** 前缀（`sensorx.sum_xor`、`pen.uplink`），
  多包共存零冲突；重名语义：`register_fields` 抛错（规格唯一），
  `register_check_fn`/`register_field_fn`/`register_payload_extractor`
  静默覆盖（后注册者赢）——钩子重名只会发生在自己的包里，前缀纪律是防线；
- 接线 = import：包被导入即完成注册（仓库既有惯例，同呈现/绑定注册表）；
  用户包暂以显式 import 接入，配置化发现（接 ADR-020 的 decodehub.toml）
  是既定后续，不需要现在做任何事；
- **测试约定**：每个包至少一条 roundtrip 测试（已知帧 → 期望字段树 +
  一个坏帧 → 期望错误标记），见 `tests/unit/test_pack_sensorx.py`；
- **运行约束**：包在进程内**只导入一次**（重复 import/reload 会因规格
  重复注册 fail-fast——这是特性不是缺陷）；一切注册只能发生在 import 期，
  不得在工具 handler / 请求路径里注册（注册表非线程安全的写入面）；
  经 ADR-020 管线使用 `field_split` 时，改规格 = 重建管线（管线不接受
  参数 overrides）。

## 9. 常见配方

**长度前缀帧 + 尾部 CRC（Modbus 风，已实跑验证）**

```json
{ "seq": [
  { "id": "len",  "type": "u16", "display": "dec", "doc": "整帧字节数（含自身与 crc）" },
  { "id": "addr", "type": "u8" },
  { "id": "func", "type": "u8", "enum": { "3": "READ_HOLD" } },
  { "id": "body", "switch_on": "func", "cases": { "3": "rb", "*": "generic" } },
  { "id": "crc",  "type": "u16", "endian": "le", "crc": { "algo": "crc16_modbus" } }
],
"types": { "rb": { "seq": [ {"id": "n", "type": "u8", "display": "dec"},
                            {"id": "data", "type": "bytes", "size": "n"} ] },
           "generic": { "seq": [ {"id": "rest", "type": "bytes",
                                  "size": "root.len - 6"} ] } } }
```

帧 `00 09 01 03 02 AB CD <crc>` → `len=9 | func=READ_HOLD | body={n=2 data=abcd}`；
帧 `00 08 01 2B 00 FF <crc>` → generic 分支按 `root.len - 6`（已消耗 4B + crc 2B）
切出 `rest=00ff`。注意：`len` 与实际长度的一致性由 CRC 兜底，表达式拿不到
"已消耗字节数"，跨分支总长核对做不了——这是 v1 边界。

**TLV 流**：`repeat: "until"` + `t == 0xFF`；或定长项 `repeat: "eos"`。

**位标志 + 条件字段**：`b1` 拆标志位 → 后续字段 `"if": "alarm | ovr"`。

**跨层长度**：子结构里 `"size": "parent.bLength - 2"`。

**双判别**：连续两个 switch（`msg_type` → `msg_class`）各自 cases，互不干扰。

## 10. 陷阱清单

- `size`/`terminator` 的单位是**字节**；位级动态长度不支持（Kaitai 同款弱项）；
- 位域 MSB 在前、共享位游标；位域之后的首个非位域字段对齐到字节边界；
- `until` **含**满足条件的末项；
- `switch_on` 判别字段必须先声明（编译期检查）；
- `crc.over` v1 只有 `prefix`（本层起点→本字段前）；
- 枚举/`cases` 键是字符串，`"0x10"` 会按十六进制解析；
- `contents` 隐含定长；`size` 为 0 合法（空 bytes）；
- 有符号数默认按补码解释；`display: "bcd"` 按 X.YZ 十六进制位组翻译；
- `value`/`if` 不作用于计算字段（value 不消耗字节、不参与重复/条件）；
- 校验函数返回值必须落在字段位宽内，否则永远 `crc` 错；
- 枚举/`cases` 键按 `int(k, 0)` 解析，**禁前导零**（`"01"` 编译期报错）；
- bytes/str 不支持 `valid.eq`（用 `contents`）；移位量上限 4096；
- 重复项若零字节推进且无截断（如 eos × 无匹配 switch），守卫会以
  `no-progress` + `incomplete` 终止——这是防挂死保护，不是数据正确。

## 11. 调试

- 规格错误（未知类型/名字/钩子未注册/宽度不符）→ `compile_spec`/
  `register_fields`/建图时即抛 `FieldSpecError`，报错带 `where` 路径
  （`root[3].body`）；
- 数据错误 → 看字段树 `errors` 标记，截断会冒泡成 `incomplete`；
- 快速试一个载荷：`examples/run_usb.py <hex> [device|config]`；
  协议包解析见 `examples/packs/sensorx/`。

## 12. 已知边界（v1）

CRC 任意区间 `over:{from,to}`、位级动态长度、流式半包粘包（Spicy 类问题）、
聚合校验跨字段计算（`value` 表达式无循环/聚合，只能具名函数）。
