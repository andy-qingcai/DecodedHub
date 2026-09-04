# ADR-020 · 管线组合：项目级单一逻辑图（tap 上游锁 → 节点链 → 独立报告 sink）

- 状态：已接受（2026-09-04）
- 背景：平台的目标形态是**整个系统的数据流是一张图**——同一份采集可以同时
  "总线解码 → 时序渲染"与"基于解码出的报文再做上层解析 → 独立导出/渲染"。
  引擎侧 DAG、跨源扇入（ADR-011）、events→events 通用节点（`event_filter`，
  及 ADR-016 的 `field_split`）都已就绪，缺的是**组合层**：没有办法把"某个
  协议锁的输出 → 再接任意节点链"声明成一个独立的分析产物。

## 决策

1. **管线 = 特殊的 ProtocolLock**（应用层 `services.bind_pipeline`）：
   键 `源|管线名`，`protocol` 字段 = 管线名，`params._pipeline` 标记。
   报告/分页/导出/渲染的既有 sink 语义全部按锁键工作——`get_events` /
   `export_events` / `render_timing` 用 `protocol=管线名` 选择，一次
   `run_decode` 产出多份**互相独立**的报告/导出/渲染，会话层零特判。
2. **图构建**：克隆上游锁子图（`bindings.clone_graph`，前缀 `up_`；与锚扇入
   同一条"结构化根/汇判定"，ADR-014）→ 汇.out → 节点链 → 链尾。链上节点
   必须：已注册、单输入、端口类型严格相等（构建期报错）。**管线可再被 tap**
   （链上链）。报告输出节点统一为**图汇**（`_report_node`，结构性判定）——
   协议图汇即 `*_decode`，语义与原硬编码完全一致，管线图汇即链尾。
   链步骤写法归一（`normalize_chain_steps`，工具与 toml 共用）：**扁写**
   `{"type": "event_filter", "kinds": [...]}`（参数平铺，推荐）与嵌套
   `{"type": …, "params": {…}}` 语义等价；混用报错。
3. **声明入口两条**：MCP 工具 `bind_pipeline(name, tap, chain)`（READY 阶段，
   工具 18→19）；`decodehub.toml` 的 `[runs.*.pipelines.<名>]`（tap + chain，
   CLI 一次运行多 sink）。管线锁**不入档案**（ADR-009 档案=源+协议锁语义不变），
   会话内重建或经 toml 声明持久化。
4. **多 agent 并发**：隔离单元是进程/会话（每 MCP 连接、每次 CLI run 各自
   独立 SessionState）；共享物是**文件**（decodehub.toml / profiles/ / reports/）。
   硬化措施：`save_profile` 原子写（tmp+rename，并发保存不产生半截 JSON）；
   管线以命名声明进 toml，可提交评审、可各自声明互不干扰；并发 run 写同一
   run 目录仍是未定义行为（约定用不同 run 名/`--out`）。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| render/export 作为图节点 | 渲染是副作用（写 PNG）+ 双输入（波形+事件），会打破拉式求值的纯函数性与 memo 幂等；先以"每报告独立渲染"满足需求，待需要窗口化渲染再议 |
| 跨图引用上游节点（不克隆） | 引擎按图求值，无跨图 memo；克隆是 ADR-011 已验证的模式 |
| 管线独立于锁的新会话对象 | 报告/导出/渲染全要加一套分派分支；复用锁键零特判 |
| 管线入档案 | 档案是"仪器与接线"的固化，管线是分析策略；进 toml 更适合评审 |

## 后果

- 正面：用户的"解析 UART 同时 render；基于报文再解析、分开输出"落地为
  两条声明；`field_split`（ADR-016）合并后即成管线的头号用户；工具/配置/
  headless 三入口共享同一 `bind_pipeline` 语义。
- 负面：tap 子图与上游锁各自求值，memo 不跨键（克隆段重复计算，可接受）；
  管线不支持 overrides（改上游参数后重建管线）；克隆使 inspect_graph 里
  上游节点出现 `up_` 前缀副本（与锚扇入一致的心智模型）。
