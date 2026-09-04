# ADR-019 · 渲染路由与导出格式注册表：呈现策略随 graph_kind/格式键走

- 状态：已接受（2026-09-04）
- 背景：ADR-013 已把**协议特化**呈现（中文名/内容列/CSV 专有列）解耦到
  protocols/<p>/present.py；但**横切呈现分派**仍硬编码在应用层——
  ① 导出格式的清单抄了四份：services.export_events 的 if/elif + ext 字面量
  dict、config.EXPORT_FORMATS 元组、runner.py 的 `("csv","json","md")` 内联
  列表、export_events 工具的 schema enum；② render_timing 用 graph_kind
  if/elif 选择画法（sliced 特判 / 其余落模拟分支），新增呈现形态必须改应用层；
  ③ 制品文件名序号靠"路径含 timing/analog 子串"扫描，命名规则散在两处。
  本决策是 ADR-014/018 单一登记点模式在呈现侧的落地。

## 决策

1. **导出格式注册表**（render/format.py）：`ExportFormatSpec`（key、ext、
   description、exporter: DecodeReport→str）+ `EXPORT_FORMAT_SPECS`（注册序
   csv/json/md）+ `export_report(fmt, report)`。config 元组、工具 enum、runner
   遍历序全部派生自它；`events_markdown` 保留为共用表格生成器（get_events/
   run_decode 预览不走导出注册表）。
2. **渲染路由注册表**（render/routes.py）：`RenderRoute`（key=graph_kind、
   label、plot(RenderInput)、needs_slice）+ `RenderInput` 数据类统一两种叶子
   渲染器的调用形状；`ROUTES` 登记 digital/sliced → `timing_plot`、
   analog_direct/fan_in → `analog_plot`（波形+事件 span）。`render_timing`
   缩为：查路由 →（needs_slice 时 `_materialize_slice` 从图 memo 物化波形）
   → `route.plot(RenderInput)` → 登记制品 → 统一构造 Markdown 表。
3. 制品命名收敛为 `_next_figure_path(store, capture_id, stem)`（按登记表
   startswith 计数，替换"路径含子串"扫描）；analog 图序号改为分通道计数。
4. 一致性测试（tests/unit/test_render_registry.py）：v1 三格式登记完整、
   config/工具 enum == 注册表、未知格式报错列出可用键、**全部绑定在数字/
   模拟两种源模态下产生的 graph_kind 必有路由**（路由缺口扫出）、sliced
   物化标记与模拟策略共享关系钉住。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 维持四处格式清单（现状） | 新增导出格式要改 4 个文件；漏改只在运行期暴露 |
| 叶子渲染器直接当路由值（不包 RenderInput） | timing_plot/analog_plot 签名不同，应用层仍需按类型分支组装参数——分派只是换地方 |
| 路由表放进 decode/bindings（随协议走） | 呈现策略是 C4 职责（ADR-006/013 边界）；decode 侧只产出 graph_kind 元数据，不感知绘图 |
| md 导出器升级为带协议头的完整报告 | 超出本次解耦范围；保持 `events_markdown(report.events)` 输出逐字节不变，演进留待需要时 |

## 后果

- 正面：新增导出格式 = format.py 登记一条（config/工具/runner 自动跟进）；
  新增呈现形态 = plots.py 加渲染器 + ROUTES 登记一行，应用层零改动；
  render_timing 从 45 行双分支缩为线性五步；文件命名规则全库一份。
- 负面：routes.py/format.py 各多一层注册间接（调试时需查表）；sliced 的图题
  从"解码时序"变为"切片时序"、analog 图序号分通道计数（文件名与旧序列略异）；
  render_timing 返回的 Markdown 表对模拟直达路由也按时间窗过滤（原为全量，
  视为修正而非兼容负担）。
