"""多源并行分析 MCP 冒烟（ADR-008 v1.2：各源独立协议锁与解码，互不影响）。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from decodehub.decode.synth import encode_i2c, encode_uart, save_kingst_csv
from decodehub.mcp_server.server import build_server


def _text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


@pytest.fixture
def two_sources(tmp_path) -> tuple[Path, Path]:
    """源 A（"la"）：I2C 总线；源 B（"probe"）：一路 UART —— 各设备采各的信号。"""
    i2c = encode_i2c([{"addr": 0x51, "read": False, "data": [0xDE, 0xAD]}], freq=100e3)
    uart = encode_uart(b"CH1,100.0,CH2,200.0\n", baud=115200)
    f1 = tmp_path / "bus_i2c.csv"
    f2 = tmp_path / "sensor_uart.csv"
    save_kingst_csv(i2c, f1)
    save_kingst_csv(uart, f2)
    return f1, f2


@pytest.mark.anyio
class TestMultiSourceMcp:
    async def test_parallel_locks_and_decode(self, two_sources, tmp_path, monkeypatch):
        f1, f2 = two_sources
        monkeypatch.chdir(tmp_path)
        async with create_connected_server_and_client_session(build_server()) as client:
            # 两个源
            await client.call_tool("lock_source", {"path": str(f1), "options": {"alias": "la"}})
            res = await client.call_tool("add_source", {
                "path": str(f2), "options": {"alias": "probe"}})
            assert "la" in _text(res) and "probe" in _text(res)
            assert len((await client.list_tools()).tools) == 11

            # 各源独立锁不同协议
            res = await client.call_tool("lock_protocol", {
                "protocol": "i2c", "source": "la"})
            assert "锁 `la|i2c` 已锁定" in _text(res)
            res = await client.call_tool("lock_protocol", {
                "protocol": "uart", "source": "probe", "params": {"baud": "auto"}})
            assert "锁 `probe|uart` 已锁定" in _text(res)
            assert len((await client.list_tools()).tools) == 19

            # 多源时缺省 lock_protocol → 引导指定 source
            res = await client.call_tool("lock_protocol", {"protocol": "uart"})
            assert res.isError and "source" in _text(res)

            # run_decode 一次并行解码全部已锁源
            res = await client.call_tool("run_decode", {})
            text = _text(res)
            assert "2 个协议锁并行解码" in text
            assert "源 `la`" in text and "源 `probe`" in text
            assert re.search(r"W 0x51 \[DE AD\]", text)
            assert "0x43 'C'" in text  # 'C' from UART payload

            # 按源取事件 / 渲染
            res = await client.call_tool("get_events", {"source": "la", "kind": "i2c.transfer"})
            assert "I2C·传输" in _text(res)
            res = await client.call_tool("get_events", {"source": "probe", "limit": 5})
            assert "UART" in _text(res)

            res = await client.call_tool("render_timing", {"source": "la"})
            imgs = [c for c in res.content if c.type == "image"]
            assert imgs and base64.b64decode(imgs[0].data)[:8] == b"\x89PNG\r\n\x1a\n"
            res = await client.call_tool("render_timing", {"source": "probe"})
            assert [c for c in res.content if c.type == "image"]

            # 单报告工具在多报告时缺省 → 引导
            res = await client.call_tool("export_events", {"format": "md"})
            assert res.isError and "source" in _text(res)
            res = await client.call_tool("export_events", {"format": "json", "source": "probe"})
            assert "已导出" in _text(res)

            # 解锁单源不影响另一源
            res = await client.call_tool("unlock_protocol", {"source": "la"})
            assert "probe" in _text(res)
            res = await client.call_tool("run_decode", {})
            assert "源 `probe`" in _text(res) and "源 `la`" not in _text(res)
            # 全部解锁 → 回 SOURCE_LOCKED
            await client.call_tool("unlock_protocol", {"source": "probe"})
            assert len((await client.list_tools()).tools) == 11

    async def test_add_source_keeps_locks(self, two_sources, tmp_path, monkeypatch):
        """追加源不使已锁协议失效（各源时间轴独立，ADR-008 v1.2）。"""
        f1, f2 = two_sources
        monkeypatch.chdir(tmp_path)
        async with create_connected_server_and_client_session(build_server()) as client:
            await client.call_tool("lock_source", {"path": str(f1), "options": {"alias": "la"}})
            await client.call_tool("lock_protocol", {"protocol": "i2c", "source": "la"})
            res = await client.call_tool("add_source", {"path": str(f2), "options": {"alias": "p2"}})
            assert "已锁" in _text(res) and "la" in _text(res)
            # la 的锁仍在，可继续解码
            res = await client.call_tool("run_decode", {"source": "la"})
            assert "W 0x51" in _text(res)
