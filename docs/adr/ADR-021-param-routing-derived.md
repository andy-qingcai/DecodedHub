# ADR-021 · 参数路由派生化：Node.PARAMS 单一权威，全部解码参数皆可配

- 状态：已接受（2026-09-05）
- 背景：需求方提出"每个 decode 的参数都可以配置"。核查发现参数路由仍是
  ADR-014 遗留的**手工白名单**（`binding.decoder_params/precond_params/
  slicer_params`）：节点新增 PARAMS 后必须再改绑定才能从工具/配置传入；
  更危险的是**拼错的参数被静默忽略**——按默认值继续解码，错误结论无从察觉。

## 决策

1. **路由派生**（`bindings.node_routed_params`）：进入节点的参数 =
   `tool_params ∩ Node.PARAMS`（排除 `role_param` 占用的通道名——那些由
   channel_map 填充）。三个白名单字段退役（保留于 dataclass 仅为既有注册
   兼容，引擎不再读取）。新参数只需加在节点 PARAMS，工具/toml/管线链
   立即可配，零登记。
2. **未知参数严格报错**（`lock_protocol`）：`params` 里不能路由到任何节点/
   角色/会话级（`tool_params_doc`）的键 → `ProtocolLockError` 列出未知键与
   全部可配参数。与配置层"未知字段报错"同一防拼写哲学（ADR-015）。
3. **参数参考命令**：`decodehub params [协议]` 列出协议全部可配参数
   （含角色与说明），数据源与校验同源（`PROTOCOL_CATALOG` ← `Node.PARAMS.doc`），
   不会漂移。管线链节点参数此前已全量可配（构造期 PARAMS 校验），本次在
   文档与测试中显式锚定。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 保留白名单并补全 | 手工同步还会再漂移；这正是本次要消除的副本 |
| 未知参数仅告警不报错 | 解码平台里静默默认值比报错危险得多（错误结论） |
| parameters schema 下发 MCP JSON Schema | list_capabilities/params 已派生同源文档，收益重复 |

## 后果

- 正面：全部解码参数（协议/预条件/切片器/管线链节点）一处声明即可配；
  typo 前置暴露；参数文档、目录、校验三者同源（PARAMS）不再可能漂移。
- 负面：此前被静默忽略的垃圾参数现在会失败（正是目的，但属行为变更）；
  绑定文件里的退役字段成为噪声，随下次触碰各协议绑定时清理。
