# ADR-013 · 呈现注册表：协议特化呈现移到协议侧（decode/presentation.py）

- 状态：已接受（2026-09-03）
- 背景：`render/format.py` 硬编码了全部协议的呈现细节——`_KIND_CN` 中文类型名表、
  `_detail` 的 `isinstance(UartEvent/I2cEvent/SpiEvent/UplinkEvent)` 内容列分支、
  `report_csv_rows` 的协议专有列取值，`render/plots.py` 硬编码 `("uart","i2c","spi")`
  时序图 span 白名单，`app/services.py` 硬编码 preview kind 元组。新增协议必须改
  render/app 三处，违背"引擎/网关/呈现零改动"的扩展承诺（ADR-012 重申）；
  且 render 反向 import 协议事件类，形成呈现→领域的特化耦合。
- 评估中顺带发现：`run_decode` 摘要的 preview 元组漏了 `downlink.packet`
  ——下行解码摘要永远不出事件表（downlink render_timing 用例的"下行·包"
  来自 events_markdown，掩盖了该缺口）。

## 决策

- 新建 `decode/presentation.py`：`Presentation(protocol, kind_cn, detail_fn, csv_columns,
  plot_family, preview_kinds)` + `register_presentation`（重复 protocol 抛 ValueError，
  仿 registry）/`presentation_of`（kind 前缀族匹配）/`all_preview_kinds`。只依赖 events。
- 每协议 `protocols/<p>/present.py` 注册（`__init__.py` 一行导入触发，跟随解码器
  注册链）；现有硬编码数据逐字节搬移——Markdown 表、CSV 列序与行为不变。
- render 消费点改为查表 + fallback：未注册 kind → 类型=原文、内容=label、CSV 协议列
  留空。`format.py` 删除全部协议事件类 import（本次解耦的核心验收点）；
  `plots.py` 白名单改查 `plot_family`；`services.py` preview 改 `all_preview_kinds()`。
- CSV 表头保持现状固定并集序：公共 8 列 + 协议列按注册顺序拼接（列名先到先得）
  ——既有 17 列逐字节不变，downlink 新列（fc_hz/slot/frame）自然追加在尾；
  只有行所属协议注册的列才填值。
- downlink 补注册（中文名/CSV 专有列/preview），修复 preview 缺口。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| render 按协议拆子目录（render/uart.py、render/i2c.py…） | 绘图/表格骨架 95% 通用，协议差异只是几行数据；拆目录制造 5 份重复骨架与 5 处公共逻辑改动点 |
| 事件类自带 `detail()`/`kind_cn` 方法（呈现逻辑进 events.py） | events 是跨层发布语言，塞呈现文本使其膨胀；且仍需 render 逐 kind 分发或 duck-typing |
| render 定义 Protocol 协议反向注册（decode→render 侧向依赖） | 依赖方向倒置：decode 是核心域，不得依赖呈现层 |

## 后果

- 正面：新增协议 = protocols/<p>/ 四件套 + 一行导入，render/app 零改动即获得
  Markdown/CSV/preview/时序图 span 全套呈现；render 不再 import 协议事件类；
  downlink 摘要出事件表。
- 负面：呈现约定与呈现代码分离两处（协议目录 vs render），靠 ADR 与协议 README
  "呈现约定"小节对齐；CSV 并集列序依赖注册顺序稳定（protocols/__init__ 固定）。
- 迁移为逐字节搬移，127 个既有测试全过 + 新增注册表单测即等价性证明。

## 附：实施中发现并修复的既有 bug

- `analog_plot` 超过 600KB 降 dpi 递归重绘时丢失 `events` 参数（ADR-010 的帧 span
  在降档重绘后消失）——已修复为透传；`timing_plot` 递归路径无此问题。
- `run_decode` 摘要 preview 元组硬编码漏 `downlink.packet`（摘要表显示"无事件"
  而实际已解出）——已由 `all_preview_kinds()` 修复（本 ADR 主决策的直接收益）。
