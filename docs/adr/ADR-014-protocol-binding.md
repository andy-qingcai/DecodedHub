# ADR-014 · 协议绑定：图模板/角色/参数路由声明随协议走（decode/bindings.py）

- 状态：已接受（2026-09-04）
- 背景：2026-09-03 代码评审发现，协议知识存在**三个必须人工同步的副本**——
  `Node.PARAMS`（权威，参与校验）、应用层 `PROTOCOL_CATALOG["params"]`（手写文案）、
  `lock_protocol` 的每协议 if/elif 分支与参数白名单元组。该漂移已产生真实缺陷
  （run_decode 预览 kind 硬编码漏 `downlink.packet`，由 ADR-013 修复呈现侧；
  本次消除模板侧）。ADR-012 曾否决"协议包收纳图模板"，理由是避免 protocols 反向
  依赖 app——方向正确，但解法不该是"模板留在 app"：图模板是**核心域知识**
  （C3 拥有图），住在应用层违背限界上下文。

## 决策

1. 新建 `decode/bindings.py`：`ProtocolBinding` 声明（协议键、解码节点 type、角色、
   可选角色、通道数需求、hint、模拟直达、预条件节点、**requires_sync 锚协议**、
   解码/预条件/slicer 三张参数路由白名单、角色→节点参数名映射、工具级参数文档）
   + `register_binding`（重复/未注册节点类型即报错）。
2. `protocols/<p>/binding.py` 注册（跟随解码器注册链，`__init__.py` 一行导入）——
   **新协议 = 协议目录四件套（decode/encode/binding/README）+ 一行导入**，
   引擎/网关/呈现/应用层零改动自此对 app 层也成立。
3. `build_lock_graph` 是图模板的**唯一权威实现**（数字路径/切片路径/模拟直达/
   跨源扇入四形态）；锚子图注入点=锚图根（无入边）、sync 抽头=锚图汇（无出边）——
   结构性质，与具体协议无关；`UL_PREFIX` 前缀与 `strip_anchor_prefix` 同址定义，
   下行参数重建与首次建图共享一份逻辑。
4. 通道自动映射（别名表/序号回退/前缀后缀匹配）移入 bindings.py——域内启发式；
   锚点解析（uplink_source 定位）与同触发校验是**会话编排**，留在应用层。
5. 工具层 `PROTOCOL_CATALOG` 改为**派生**：参数文档取自 `Node.PARAMS.doc`
   （与校验同源），角色覆盖与 `uplink_source` 由绑定补充——文档三副本收敛为一。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 维持 app 层 if/elif + 白名单 | 三副本漂移已产生真实 bug；每加协议 lock_protocol 线性膨胀 |
| 锚协议子图 id/前缀散落各处（现状） | `_rebuild_downlink` 与 `lock_protocol` 手抄两份克隆逻辑，`ul_` 前缀改一处崩另一处 |
| binding 含会话编排（锚解析/t0 校验） | 依赖会话状态与源 Capture——编排是应用层职责，领域保持纯声明 |
| 把 graph_kind 判定留在 render 嗅探节点 id | 节点 id 是构建细节；结构化元数据（ADR 前置修复）+ `binding.graph_kind_for(cap)` |

## 后果

- 正面：新协议产物明确且自包含；`lock_protocol` 从 ~140 行 if/elif 缩为 ~60 行编排；
  参数文档单一来源；图构建逻辑全库一份；`fan_in` 图形状元数据随绑定泛化
  （第二个扇入协议零新增分派）。
- 负面：协议目录多一个文件（binding.py）；`PROTOCOL_CATALOG` 从字面量变为派生值
  （调试时需查绑定表）；节点 id 约定统一为类型名（原 uplink 预条件节点 id "upre"
  → "uplink_precond"，inspect_graph 输出变化）。
