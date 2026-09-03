# ADR-001 · 用低层 MCP Server 实现每会话渐进式工具暴露

- 状态：已接受（2026-09-03）
- 背景：需求"初始暴露接口少，锁定数据采集器与解码协议后才暴露后续接口，减小上下文"。

## 决策

采用官方 Python SDK 的**低层 `mcp.server.Server`**：

1. `@server.list_tools()` 处理器按会话阶段返回不同工具集；
2. 锁定动作内 `await ctx.session.send_tool_list_changed()` 推送 `notifications/tools/list_changed`；
3. `create_initialization_options(notification_options=NotificationOptions(tools_changed=True))` 宣告能力；
4. `call_tool` 分发前做阶段门禁（对缓存过期客户端也安全）；
5. 会话状态以 `id(ctx.session)` 为键。

以上全部已对已安装 mcp 1.29.1 做 in-memory 端到端验证（含"工具处理器内发通知→客户端重取→立即调用新工具"完整链路）。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| FastMCP 常规注册（mho98 风格，~80 工具全量暴露） | 正是要消除的上下文膨胀；其全局 ToolManager 无每会话差异能力；`run()` 不传 NotificationOptions → `listChanged=false` |
| 单一 dispatch 工具 + 描述内渐进披露 | 丧失真实 JSON schema（客户端无法校验/渲染参数）；把路由负担推给模型 |
| elicitation / sampling | 语义不符（面向用户输入/LLM 调用），且依赖可选客户端能力 |

## 后果

- 正面：初始仅 4 工具；锁定按需解锁；列表变更对支持 list_changed 的客户端（Claude Code ≥2.1.0 等）即时生效。
- 负面/风险：部分客户端不刷新列表 → 缓解 = 门禁引导错误 + lock 返回文本列出新工具名（三层保险）；需自维护 ToolSpec/分发表（~百行，可控）。
- 工具总数上限（14）与分阶段目录见 `50-mcp-gateway.md`。

## 已知坑位（实测确认）

`list_tools` 处理器可能被以 `req=None` 调用（schema 缓存刷新路径）→ 处理器参数带默认值；无 session_id → 用对象身份；无会话关闭回调 → stdio 单会话/进程模型下无需清理；stdout 为协议通道 → 日志走 stderr。
