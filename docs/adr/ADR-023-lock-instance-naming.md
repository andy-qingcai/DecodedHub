# ADR-023 · 锁实例命名：同源同协议多路并存（`源|name`）

- 状态：已接受（2026-09-05）
- 背景：用户场景"同一采集里有多个 UART"（如 LA 的 D0/D1 一路、D2/D3 另一路）。
  锁键原为 `源|协议`，同源第二把 uart 锁**静默覆盖**第一把——三条入口（MCP 工具、
  toml 内联、profile JSON）全部如此；toml 内联锁以源别名为键，同一源连写两把的
  语法都不存在。多源模型（每文件一别名）解决跨文件多 UART，解决不了单文件多路。

## 决策

1. **锁实例名**：`ProtocolLock.name`（缺省物化为协议名，完全向后兼容）。
   锁键 = `源|name`；报告键、`report.protocol`、导出文件名
   （`events-<源>-<实例名>.md`）、decoded.json 的 protocol 字段随之——
   "是哪个 UART"由命名回答，事件流彼此独立。
2. **重锁 = 显式错误**：同锁键再次 lock → `ProtocolLockError`（提示 unlock 或换
   name），绝不静默覆盖。`run_decode` 的 overrides 重建路径先 pop 后重建（内部
   替换语义不受影响）。
3. **两条声明入口**：
   - MCP `lock_protocol` 增加可选 `name` 参数；
   - toml 内联锁新增**数组形式** `[[runs.main.decode.locks]]`（每项含
     `source`/`protocol`/可选 `name`/`params`），与原表形式（每源一把）语义等价、
     可并存。profile JSON 的 locks 数组本来就支持同源多把，`LockSpec` 增加可选
     `name`（= 协议名时省略不写，旧档案零改动）。
4. **下游语义适配**：`get_events/export_events/render_timing` 的 `protocol` 参数
   接受实例名（工具 schema 去掉枚举、改为说明文字）；`unlock_protocol` 按协议或
   实例名匹配；downlink 锚解析（`uplink_source`）接受 源别名 / 实例名 / 完整键
   三种写法，缺省 = 唯一锚锁，物化为完整键供重建复用；管线 `tap = "源|实例名"`
   原生支持。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 保持一源一协议锁，多路走"同文件多别名" | 文件加载 ×N、语义别扭、toml 内联根本表达不了 |
| 事件上打"哪路 uart"标签 | 事件是单报告内的流，报告级命名已足够；给 DecodedEvent 加来源字段侵入发布语言 |
| 自动编号（uart#1/2） | 隐式命名不可评审；显式 name 才能进档案/toml 被 code review |

## 后果

- 正面：单文件多路同协议并存；报告/导出/管线 tap 全链路可辨识；重锁从静默
  覆盖变为显式错误（行为变更：依赖"重锁即更新"的外部流程需改走 overrides）。
- 负面：`_PRO` 参数失去枚举（客户端无法 schema 级提示协议名，换文档文字）；
  downlink 的 `uplink_source` 现在有三种合法写法（文档需说明，推荐完整键）。
