"""MCP 渐进式暴露冒烟测试（in-memory client↔server，docs/60-testing.md）。

断言：能力宣告、阶段过滤、lock 后 list_changed、门禁错误、READY 全链路（含 ImageContent）。
"""

from __future__ import annotations

import base64
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from decodehub.decode.synth import encode_uart, save_kingst_csv
from decodehub.mcp_server.server import build_server


@pytest.fixture
def uart_csv(tmp_path) -> Path:
    wave = encode_uart(b"decodehub progressive disclosure!", baud=115200)
    p = tmp_path / "uart_capture.csv"
    save_kingst_csv(wave, p)
    return p


def _text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


def _images(result) -> list:
    return [c for c in result.content if c.type == "image"]


@pytest.mark.anyio
class TestProgressiveDisclosure:
    async def test_full_lifecycle(self, uart_csv, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # 制品写入 CWD/out
        async with create_connected_server_and_client_session(build_server()) as client:
            # 1) 初始化能力宣告
            # (create_connected… 内部完成 initialize；用 list_tools 验证)
            tools = await client.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == ["get_session", "list_capabilities", "list_profiles",
                             "lock_source", "open_project", "reset_session"]

            # 2) 门禁：越权调用 → isError + 引导文本（客户端默认不抛异常）
            res = await client.call_tool("run_decode", {})
            assert res.isError
            gate_text = _text(res)
            assert "lock_protocol" in gate_text or "阶段" in gate_text

            # 3) lock_source → 收到 tools/list_changed + 新工具
            res = await client.call_tool("lock_source", {"path": str(uart_csv)})
            text = _text(res)
            assert "已摄取采集" in text
            assert "describe_capture" in text  # 返回文本列出新工具名（兜底）
            tools = await client.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert "describe_capture" in names and "lock_protocol" in names
            assert "add_source" in names
            assert len(names) == 11

            # 4) describe_capture
            res = await client.call_tool("describe_capture", {})
            assert "数字 `TX`" in _text(res) and "源 `" in _text(res)

            # 5) lock_protocol → READY
            res = await client.call_tool("lock_protocol", {"protocol": "uart"})
            assert "已锁定: **uart**" in _text(res)  # ADR-022：文案改为 锁`源|名` 已锁定
            tools = await client.list_tools()
            assert len(tools.tools) == 19
            assert "run_decode" in sorted(t.name for t in tools.tools)

            # 6) run_decode → 往返数据正确
            res = await client.call_tool("run_decode", {"overrides": {}})
            text = _text(res)
            assert "解码完成" in text
            expected = list(b"decodehub progressive disclosure!")
            import re
            values = [int(x, 16) for x in re.findall(r"0x([0-9A-F]{2})", text)]
            # 摘要只预览前 20 帧（完整数据走 get_events/export_events）
            assert values == expected[:20]

            # 7) render_timing → ImageContent + 配对表
            res = await client.call_tool("render_timing", {})
            imgs = _images(res)
            assert imgs and imgs[0].mimeType == "image/png"
            raw = base64.b64decode(imgs[0].data)
            assert raw[:8] == b"\x89PNG\r\n\x1a\n"
            assert len(raw) < 600_000
            text = _text(res)
            assert "时序图" in text and "|" in text  # markdown 表

            # 8) export_events
            res = await client.call_tool("export_events", {"format": "json"})
            assert "已导出" in _text(res)
            exported = list((tmp_path / "out").rglob("events.json"))
            assert exported and exported[0].stat().st_size > 100

            # 9) inspect_graph
            res = await client.call_tool("inspect_graph", {})
            assert "uart_decode" in _text(res)

            # 10) reset → 回 DISCOVERY
            await client.call_tool("reset_session", {})
            tools = await client.list_tools()
            assert len(tools.tools) == 6

    async def test_error_translation(self, uart_csv):
        async with create_connected_server_and_client_session(build_server()) as client:
            res = await client.call_tool("lock_source", {"path": "/nonexistent/file.csv"})
            assert res.isError
            assert "不存在" in _text(res) or "No such" in _text(res)

    async def test_analog_source_slicer_pipeline(self, tmp_path, monkeypatch):
        """模拟源（mho98 CSV）→ 自动切片 → UART 解码 全链路。"""
        from decodehub.decode.synth import analogify, encode_uart

        monkeypatch.chdir(tmp_path)
        # 造一个 mho98 风格 CSV（通道名取自 source= 前导）
        wave = encode_uart(b"\xA5\x5A\x00\xFF", baud=9600)
        ch = analogify(wave, "TX", fs=2_000_000, v_low=0, v_high=3.3,
                       rise_s=5e-6, noise_sigma=0.01, seed=3)
        p = tmp_path / "ch1_norm_fake.csv"
        with open(p, "w") as f:
            f.write("# MHO98 waveform source=CHANnel1 mode=NORMal points=%d\n" % ch.n)
            f.write("# xincrement=%.17g xorigin=%.17g xreference=0.0\n" % (ch.dt, ch.t0))
            f.write("t_s,v_V\n")
            for i in range(0, ch.n, 20):  # 抽稀写入
                f.write("%.9g,%.9g\n" % (ch.time_at(i), float(ch.samples[i])))

        async with create_connected_server_and_client_session(build_server()) as client:
            await client.call_tool("lock_source", {"path": str(p)})
            await client.call_tool("lock_protocol",
                                   {"protocol": "uart", "params": {"baud": 9600}})
            res = await client.call_tool("run_decode", {})
            import re
            values = [int(x, 16) for x in re.findall(r"0x([0-9A-F]{2})", _text(res))]
            assert values[:4] == [0xA5, 0x5A, 0x00, 0xFF]
            res = await client.call_tool("render_analog", {})
            assert _images(res)
