# ADR-018 · 适配器规格注册表：解析/嗅探/选项随格式走（adapters/spec.py）

- 状态：已接受（2026-09-04）
- 背景：2026-09-04 架构评估发现，采集格式的知识存在**三处必须人工同步的副本**——
  `adapters/__init__.py` 的 `ADAPTERS` dict + `PLANNED/_PLANNED_NOTES`、`sniff.py`
  的有序嗅探规则 + `SUPPORTED_FORMATS` 描述 dict、`mcp_server/tools.py` lock_source
  的 options 帮助文案（硬编码"哪个格式要哪些选项"）。加一个格式的真实成本 ≈
  6 处 / 4 个文件，其中仅 1 处是解析逻辑，其余全是登记与元数据；文案已现漂移
  （tool 描述漏 generic_csv 选项、把 mcu_adc_csv 的条件必填写成硬必填）。本决策
  是 ADR-014（协议绑定单一登记点）在采集侧的对称落地。

## 决策

1. 新建 `adapters/spec.py`：`AdapterSpec`（key、description、load、sniff 匹配器、
   sniff_hint、options 声明、planned_note）+ `OptionField`（name/type/doc/required）
   + `SniffCtx`（头部字节/文本性/文本行惰性求值，一次嗅探共享）+ 共享文本表头启发
   （TIMEISH/ADCISH/VOLT_COL）。
2. 每个适配器模块自带 `SPEC`——**解析器、嗅探匹配器、选项声明、目录描述同处一处**；
   延后支持格式（saleae 三件，ADR-007）登记于 `adapters/planned.py`（load=None，
   planned_note 必填）。
3. `adapters/__init__.py` 的 `SPECS`（插入序 = 嗅探优先序，规则 1–6 见 docs/40）是
   唯一登记表；`SUPPORTED_FORMATS` / `PLANNED_FORMATS` / capabilities 每格式选项明细
   / MCP options JSON schema / required 前置校验全部**派生**，不再手工复写。
4. 新增一个格式的全部动作：**写 `adapters/<fmt>.py`（load + SPEC）+ `SPECS` 登记
   一行**（位置即嗅探优先级）。未知/延后格式统一在注册表报错
   （`resolve_spec`）；required 选项在解析前由 `validate_options` 前置强制。
5. `sniff.py` 退化为遍历器：构建 SniffCtx → 按 SPECS 序调匹配器 → tried 诊断 →
   延后格式抛 `PlannedFormatError`。不再持有任何具体格式的规则。
6. 一致性测试（tests/unit/test_adapter_registry.py）钉住：9 支持格式登记完整
   （曾漏 kingst_bin）、supported/planned 恰好划分 SPECS、派生 schema 覆盖全部
   声明、必填标注正确、缺失必填在解析前报错。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 维持三处清单（现状） | 每格式 6 触点；tools.py 文案已漂移；同步靠自觉无测试守护 |
| 嗅探匹配器集中留在 sniff.py | 格式知识仍两处（解析在适配器、嗅探在中心）；兜底规则 6 会随二进制格式增多而更脆 |
| options 严格类型/未知键校验 | 适配器容忍 app 级键（alias/t_wall/offset）随 options 透传，严格白名单会误伤；类型宽松转换是既有语义 |
| 文本格式共用一个聚合匹配器（复刻原 if 链逐行交织） | 匹配器无法随 spec 登记，回到中心化；逐行交织顺序仅在"同一文件多行不同格式表头"这种病态输入下才有差异 |

## 后果

- 正面：加一个格式 = 1 文件 + 1 行；格式元数据单一来源，MCP schema/capabilities
  自动更新；required 缺失从"解析到一半才报"提前为统一前置报错；tried 诊断从
  聚合文案细化为逐格式提示。
- 负面：`adapters/` 包多 spec.py/planned.py 两个模块；SUPPORTED_FORMATS 的展示
  顺序由登记序决定（与旧 dict 字面量顺序略异）；tried 消息逐格式展开（更长）；
  文本嗅探在病态输入下的匹配顺序由"行内交织"变为"格式优先"（真实 CSV 单表头，
  无实际影响）。
