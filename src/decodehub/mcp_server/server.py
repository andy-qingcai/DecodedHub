"""MCP 网关装配（ADR-001）：低层 Server + 每会话 list_tools 过滤 + 门禁。

- stdout 是协议通道：日志一律走 stderr；
- 会话状态以 id(ctx.session) 为键；
- lock/reset 引起阶段变化 → send_tool_list_changed；
- 门禁：工具不可见时返回可操作的引导错误（防客户端列表缓存过期）。
"""

from __future__ import annotations

import base64
import logging
import sys
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from ..app.session import SessionState, Stage
from ..shared.errors import DecodehubError, StageGateError
from .tools import TOOLS, TOOLS_BY_NAME, stage_tool_names, visible

log = logging.getLogger("decodehub")

_INSTRUCTIONS = (
    "采集数据统一解码平台：先 lock_source 摄取采集文件（逻辑分析仪/示波器/MCU ADC 导出），"
    "再 lock_protocol 锁定协议（uart/i2c/spi），之后 run_decode 解码、render_timing 出图、"
    "export_events 导出。工具按阶段解锁（tools/list_changed）。"
)


def build_server() -> Server:
    server = Server("decodehub", instructions=_INSTRUCTIONS)
    sessions: dict[int, SessionState] = {}

    def state_for(session) -> SessionState:
        key = id(session)
        if key not in sessions:
            sessions[key] = SessionState()
        return sessions[key]

    @server.list_tools()
    async def list_tools(req: types.ListToolsRequest | None = None):  # req 可能为 None（缓存刷新）
        try:
            state = state_for(server.request_context.session)
        except LookupError:
            state = SessionState()
        stage = state.stage
        return [
            types.Tool(name=t.name, description=t.description, inputSchema=t.schema)
            for t in TOOLS
            if visible(stage, t.stage)
        ]

    def _to_content(items: list) -> list[types.Content]:
        out: list[types.Content] = []
        for it in items:
            if isinstance(it, Path):
                data = base64.b64encode(it.read_bytes()).decode()
                out.append(types.ImageContent(type="image", data=data, mimeType="image/png"))
            else:
                out.append(types.TextContent(type="text", text=str(it)))
        return out

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None):
        ctx = server.request_context
        state = state_for(ctx.session)
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=f"未知工具 {name!r}；可用: {stage_tool_names(state.stage)}"))
        if not visible(state.stage, spec.stage):
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=StageGateError(
                    name, spec.stage.value, state.stage.value,
                    hint=f"当前可用: {stage_tool_names(state.stage)}",
                ).args[0],
            ))
        args = dict(arguments or {})
        before = state.stage
        try:
            # 同步领域调用放线程池，避免阻塞事件循环（大文件/长解码）
            result = await anyio.to_thread.run_sync(spec.handler, args, state)
        except DecodehubError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e))) from e
        except FileNotFoundError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e))) from e
        except Exception as e:  # 非预期错误：日志留痕 + 内部错误码
            log.exception("工具 %s 执行失败", name)
            raise McpError(ErrorData(code=-32603, message=f"{type(e).__name__}: {e}")) from e
        if state.stage != before:
            try:
                await ctx.session.send_tool_list_changed()
            except Exception:  # 通知失败不致命（门禁兜底）
                log.warning("send_tool_list_changed 失败", exc_info=True)
        return _to_content(result)

    return server


async def _amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(
            read, write,
            server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)
            ),
        )


def main() -> None:
    anyio.run(_amain)


if __name__ == "__main__":
    main()
