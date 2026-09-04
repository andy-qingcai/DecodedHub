"""协议客制渲染注册表（ADR-022）：(protocol, graph_kind) 键的客制路由，通用路由兜底。

多 agent 扩展面：每协议一个 contrib 模块文件（pkgutil 零登记发现），重复注册
import 期抛错。这里的守卫：分派两级优先级、fallback 恒可达、文件投放即生效。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.synth import encode_uart, save_kingst_csv
from decodehub.render import contrib
from decodehub.render.routes import RenderRoute, render_route
from decodehub.shared import DecodehubError

PROBE_KEY = ("__probe__", "digital")


def _stub_plot(inp):
    inp.path.write_bytes(b"contrib")
    return inp.path


def _cleanup(key):
    contrib._CONTRIB.pop(key, None)


@pytest.fixture()
def uart_state(tmp_path):
    csv = tmp_path / "cap.csv"
    save_kingst_csv(encode_uart(b"HELLO", baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1), csv)
    st = SessionState()
    services.ingest(st, str(csv), None, None)
    services.lock_protocol(st, "uart", {"baud": 115200}, None)
    services.run_decode(st, None, None)
    return st


class TestContribRegistry:
    def test_unregistered_falls_back_to_generic(self):
        assert contrib.contrib_route("can", "digital") is None
        assert contrib.resolve_route("can", "digital") is render_route("digital")

    def test_contrib_route_wins_over_generic(self):
        custom = RenderRoute("digital", "客制时序", _stub_plot)
        contrib.register_contrib_route(PROBE_KEY[0], PROBE_KEY[1], custom)
        try:
            assert contrib.contrib_route(*PROBE_KEY) is custom
            assert contrib.resolve_route(*PROBE_KEY) is custom
            # 同协议其他形状、他协议同形状仍走通用路由
            assert contrib.resolve_route("__probe__", "sliced") is render_route("sliced")
            assert contrib.resolve_route("can", "digital") is render_route("digital")
        finally:
            _cleanup(PROBE_KEY)

    def test_duplicate_registration_raises(self):
        contrib.register_contrib_route(PROBE_KEY[0], PROBE_KEY[1],
                                       RenderRoute("digital", "a", _stub_plot))
        try:
            with pytest.raises(ValueError, match="重复注册"):
                contrib.register_contrib_route(PROBE_KEY[0], PROBE_KEY[1],
                                               RenderRoute("digital", "b", _stub_plot))
        finally:
            _cleanup(PROBE_KEY)

    def test_unknown_kind_still_reports_available(self):
        with pytest.raises(DecodehubError, match="analog_direct"):
            contrib.resolve_route("can", "constellation")


class TestRenderTimingDispatch:
    def test_contrib_route_renders(self, uart_state):
        seen = {}

        def plot(inp):
            seen["title"] = inp.title
            inp.path.write_bytes(b"contrib")
            return inp.path

        contrib.register_contrib_route("uart", "digital", RenderRoute("digital", "客制时序", plot))
        try:
            p, _table = services.render_timing(uart_state, None, None, 60, 150, source="cap")
            assert p.read_bytes() == b"contrib"
            assert "客制时序" in seen["title"]
        finally:
            _cleanup(("uart", "digital"))

    def test_pipeline_sink_hits_family_route(self, uart_state):
        """管线报告 protocol 字段是管线名；分派族从事件 kind 前缀推导（同 presentation_of）。"""
        services.bind_pipeline(uart_state, "frames", "uart",
                               [{"type": "event_filter", "params": {"kinds": ["uart.frame"]}}])
        services.run_decode(uart_state, None, None)
        contrib.register_contrib_route("uart", "digital",
                                       RenderRoute("digital", "客制时序", _stub_plot))
        try:
            p, _table = services.render_timing(uart_state, None, None, 60, 150,
                                               source="cap", protocol="frames")
            assert p.read_bytes() == b"contrib"
        finally:
            _cleanup(("uart", "digital"))


PROBE_MODULE_SRC = '''"""测试探针：验证 contrib 模块投放即被发现（ADR-022）。"""
from ..routes import RenderRoute
from . import register_contrib_route


def _plot(inp):
    inp.path.write_bytes(b"probe")
    return inp.path


register_contrib_route("__probe__", "digital",
                       RenderRoute("digital", "探针时序", _plot))
'''


class TestDiscovery:
    def test_dropped_file_loaded_without_registration(self):
        pkg_dir = Path(contrib.__file__).parent
        probe = pkg_dir / "zz_probe_contrib.py"
        mod_name = "decodehub.render.contrib.zz_probe_contrib"
        probe.write_text(PROBE_MODULE_SRC, encoding="utf-8")
        sys.modules.pop(mod_name, None)
        try:
            contrib.load_contrib_modules()
            assert contrib.contrib_route("__probe__", "digital") is not None
        finally:
            probe.unlink(missing_ok=True)
            sys.modules.pop(mod_name, None)
            _cleanup(PROBE_KEY)
