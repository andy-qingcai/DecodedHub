# ADR-022 · 协议客制渲染注册表：(protocol, graph_kind) 二级分派，通用路由兜底

- 状态：已接受（2026-09-05）
- 背景：ADR-019 的路由表只按 graph_kind 分派，叶子渲染器仅 timing_plot /
  analog_plot 两种通用形态——协议对"怎么画"的唯一影响是 `plot_family` 一个
  bool（画不画 span）。预期协议会陆续出现**自身最佳显示**（星座图/眼图/包结构
  图/解扩视图……），通用时序图表达不了。本决策把 ADR-013/018/019 的单一登记点
  模式延伸到图的协议维度；**多 agent 并行开发**是硬约束：扩展面必须是"每协议
  自己的文件"，不能让协议特化逻辑长回 plots.py/routes.py 的 if/elif（那正是
  ADR-013/019 消灭掉的合并热点）。

## 决策

1. **客制注册表**（render/contrib/__init__.py）：键 `(protocol, graph_kind)` →
   `RenderRoute`（复用 ADR-019 的数据类，label/plot/needs_slice 语义不变）。
   `register_contrib_route` 重复键抛 ValueError（import 期 fail-fast，两个
   agent 抢同一键立刻暴露而非静默覆盖）；分派是 key 查找，**与注册顺序无关**
   （比 CSV 并集列序的顺序耦合对并行注册友好，见 ADR-013 负面项）。
2. **两级分派**：`resolve_route(protocol, graph_kind)` = 客制路由优先 →
   `render_route(graph_kind)` 通用兜底 → 未登记显式报错。`render_timing` 查表
   前先推导**分派族**：首事件 kind 前缀（与 `presentation_of` 同一规则）——
   管线报告（ADR-020）的 `report.protocol` 是管线名而非协议族，不可直接用；
   空报告退 `report.protocol`。
3. **零登记发现**：contrib 包 import 末尾 pkgutil 扫描本目录并导入全部模块；
   新增协议客制图 = **投放一个 `render/contrib/<protocol>.py` 文件**，无任何
   共享文件改动。contrib 模块可 import 协议事件类（render→decode 方向合法，
   routes.py 已有先例），matplotlib 仍只住呈现上下文。
4. 一致性测试（tests/unit/test_render_contrib.py）：fallback 恒可达（未注册
   协议返回通用路由对象）、客制优先且不外溢（同协议异形状、异协议同形状仍走
   通用）、重复注册报错、render_timing 端到端走客制路由（含 label 进图题）、
   管线 sink 按事件族命中、**探针文件投放即被发现**（测试向 contrib 目录写入
   临时模块并验证 pkgutil 加载，finally 清理）。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 照抄 decode 注册方向：协议包（decode 侧）注册渲染器 | 协议包须 import render → 依赖倒置；ADR-013/019 已两次否决（decode 是核心域，不依赖呈现层）。参考的是注册表模式，不是注册代码的住址 |
| Presentation 加数据级绘图字段（span 样式/标注 fn） | 表达力封顶于字段词表，异形图（星座/眼图）装不下；词表会膨胀成第二门绘图 DSL。留作轻量场景（仍是时序图、只调标注样式）的过渡，不作为机制 |
| 协议特化逻辑写进 plots.py/routes.py | 每协议改共享文件，多 agent 下即合并热点；复刻 ADR-013/019 消灭的反模式 |
| contrib 模块经 protocols/__init__ 导入链触发 | 同方案 1：decode 侧 import render，方向非法 |

## 后果

- 正面：协议"自身最佳显示"有了正式挂点且不触碰任何共享文件；现有 5 协议零
  改动、行为不变（无 contrib 登记时逐字节走 ADR-019 路径）；客制渲染器直接
  复用 `RenderInput`/`_next_figure_path`/制品登记与 Markdown 表管线。
- 负面：协议特化绘图代码与协议解码代码分居两个上下文
  （`render/contrib/<p>.py` vs `decode/protocols/<p>/`）——与 present.py 同款
  "约定与代码分离"成本（ADR-013 已接受），靠协议 README"呈现约定"小节与
  本 ADR 对齐；调试多一层注册间接；混族报告（fan_in 含锚子图事件）按首事件
  族分派，客制渲染器需自行处理异族事件。
- 触发时机：首个"通用时序图表达不了"的需求出现时，协议作者只需新建 contrib
  文件，本决策的基础设施已就位（本 ADR 即随该基础设施落地）。
