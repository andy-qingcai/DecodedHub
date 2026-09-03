# 10 · 统一语言（Ubiquitous Language）

> 状态：已定案 · 术语中英对照，代码一律使用英文标识符，文档以中文为主

以下术语在整个代码库、文档、MCP 工具描述中含义唯一。新增概念必须先进入本表。

## 采集与信号

| 术语 | 英文/代码标识 | 定义 |
|---|---|---|
| 采集记录 | `Capture` | 一次采集会话的统一根对象：元数据 + 数字组 + 模拟通道列表。锁定数据源后由系统持有。 |
| 采集元数据 | `CaptureMeta` | 来源种类、设备名、采样率、触发时刻、阈值、采集时间等描述性字段。 |
| 数字组 | `DigitalWave` | **多通道数字信号的紧凑 IR**：通道名表、初始位域、严格递增跳变时刻数组 + 每次跳变后的位域快照、结束时刻。是解码器的唯一数字输入。 |
| 位域快照 | `edges_levels[i]` | 第 i 次跳变**之后**所有通道的电平打包成的整数（bit k = 通道表第 k 个通道的电平）。 |
| 模拟通道 | `AnalogChannel` | 单通道电压序列：`(t0, dt, n)` 紧凑时间轴（非均匀时才存 times 数组）+ float32 样本 + 单位。 |
| 跳变 | edge / transition | 数字信号的一次电平变化。所有解码算法在跳变流上运行，不重采样。 |
| 时间轴基准 | `TimeBase` | `trigger_relative`（触发点为 0，Kingst/MHO98 默认）或 `absolute`（有 epoch 锚点）。 |
| 嗅探 | `sniff` | 依据扩展名/魔数/文件头启发式规则判定文件格式的过程。规则有序、可解释、失败时报告尝试过的规则。 |
| 适配器 | `Adapter` | 某种文件格式 → `Capture` 的翻译器（防腐层实现）。一格式一适配器。 |
| 阈值切片 | `slicer` | 模拟 → 数字的 Schmitt 式转换：上阈值 `thr + h/2` 置高、下阈值 `thr − h/2` 置低，`h` 为滞回宽度。 |

## 图与解码

| 术语 | 英文/代码标识 | 定义 |
|---|---|---|
| 图 | `Graph` | 数据加工的有向无环图：节点列表 + 边列表。**纯函数语义**：同输入同输出、无副作用。 |
| 节点 | `Node` / `NodeSpec` | 图中一个加工步骤。`NodeSpec` 是声明（id、type、params）；`Node` 是注册表中 type 对应的实现（端口签名 + run）。 |
| 端口 | `PortType` | 端口的类型词汇表：`capture` / `digital` / `analog` / `events` / `scalar`。连线两端类型必须**严格相等**，无隐式转换——模拟→数字必须显式经过 `slicer` 节点。 |
| 求值 | `evaluate` | 拉式（pull-based）记忆化求值：递归即拓扑序，只计算目标节点的祖先，输出按节点 id 缓存。 |
| 通道挑选 | `pick` | 从 `Capture` 抽取数字子集（`digital_pick`）或模拟通道列表（`analog_pick`）的节点。 |
| 解码事件 | `DecodedEvent` | 一切解码器的统一输出原子：`kind / t_start / t_end / label / errors / ann_class` + 协议专有字段。**解码错误是事件，不是异常**。 |
| 解码报告 | `DecodeReport` | 一次图执行在 `events` 端口上的完整产物：协议、参数、事件列表、运行统计。 |
| 帧化 | framing | 位流 → 帧级事件的重组（UART 帧的起止与校验、SPI 的 CS 片选区间、I2C 的 START..STOP 传输）。 |
| 自动波特率 | auto-baud | 从最短低脉冲宽度集合取中值估计位周期的方法（起始位是空闲高电平线上唯一保证的单 bit 低脉冲）。 |

## 呈现与会话

| 术语 | 英文/代码标识 | 定义 |
|---|---|---|
| 图文配对 | figure-table pairing | 时序图中帧 span 只标编号，详细内容放同一次工具返回的 Markdown 表——避免图内文字重叠。 |
| 制品 | `Artifact` | 呈现层产物（PNG/CSV/JSON/MD 文件）的登记项：路径、类型、说明、字节数。落盘于 `out/<capture_id>/`。 |
| 会话 | `Session` | 一个 MCP 客户端连接的生命周期。以会话对象身份为键持有 `SessionState`。 |
| 会话状态 | `SessionState` | 三阶段状态机 + 工程与已锁定的协议参数 / 最近解码报告 / 制品清单。 |
| 阶段 | `Stage` | `DISCOVERY` → `SOURCE_LOCKED` → `READY`。工具可见性由阶段决定（6 → 11 → 18，累积解锁；权威数字以 50-mcp-gateway 为准）。 |
| 锁定 | lock | `lock_source`（摄取首个源并创建工程）/ `add_source`（追加源）/ `lock_protocol`（选定解码协议与参数）。锁定动作触发 `tools/list_changed` 通知。 |
| 门禁 | gate | 服务端在 `call_tool` 中强制校验"该工具在当前阶段可用"；即使客户端工具列表缓存过期也安全。 |

## 多源工程（ADR-008 v1.2：并行独立分析）

| 术语 | 英文/代码标识 | 定义 |
|---|---|---|
| 工程 | `Project` | 多采集源容器：N 个 `SourceEntry`（别名 + Capture）。**各源独立分析**（v1.2）。 |
| 源条目 | `SourceEntry` | 工程中的一个采集源：别名 + Capture（+ 库级偏移/墙钟字段，见下）。 |
| 别名 | alias | 源的短名（缺省 = 文件名 slug）；工具 `source` 参数按它指定源。 |
| 协议锁 | `ProtocolLock` | 键 = **`源|协议`**（ADR-011）：一源可并存多协议锁（示波器双通道同时锁 uplink+downlink）；含协议、参数、通道映射、解码图、source_inputs（跨源注入）。工具以 source + 可选 protocol 消歧。 |
| 并行解码 | parallel decode | `run_decode()` 缺省对全部已锁源各自执行解码图，返回分节摘要。 |
| ~~锚点对齐~~ | ~~anchor alignment~~ | 跨设备共同信号自动检测——**永久否决**（用户场景不可得）。 |
| （库能力）合并/偏移 | `Project.merged` / offset | 多源合并到公共时间轴：保留为库能力 + 单测锚定，**不暴露于工具层**（v1.2 裁决：PC 时间戳 ≥百 ms 误差、不拆总线到多设备 → 无可信偏移来源）。 |
| 同时刻容差归并 | coincidence merge | 合并跳变时把相差 ≤1e-12s（相对容差）的翻转视为同一物理时刻，规避浮点 ulp 伪先后。 |

## 工程档案（ADR-009）

| 术语 | 英文/代码标识 | 定义 |
|---|---|---|
| 工程档案 | Profile | 固化"源定义 + 各源协议锁"的 JSON（`profiles/<name>.json`）：IO 与仪器固定的重复调试一步开工程。 |
| 打开工程 | open_project | 按档案摄取各源文件并应用全部协议锁，一步直达 READY。 |
| 接线防线 | wiring guard | 档案钉死的通道角色在打开时校验；采集通道集合不符 → 立即报错（探头插错位当场暴露）。 |
