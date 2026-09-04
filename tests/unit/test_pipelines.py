"""管线组合（ADR-020）：tap 上游锁输出 → 通用节点链 → 独立报告 sink。"""

from __future__ import annotations

import pytest
from conftest import values_of  # noqa: F401

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.synth import encode_uart, save_kingst_csv
from decodehub.shared.errors import ProtocolLockError

PAYLOAD = b"HELLO PIPELINE"


@pytest.fixture()
def uart_state(tmp_path):
    csv = tmp_path / "cap.csv"
    save_kingst_csv(encode_uart(PAYLOAD, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1), csv)
    st = SessionState()
    services.ingest(st, str(csv), None, None)
    services.lock_protocol(st, "uart", {"baud": 115200}, None)
    return st


CHAIN = [{"type": "event_filter", "params": {"kinds": ["uart.frame"]}}]


class TestBindPipeline:
    def test_bind_and_run_two_sinks(self, uart_state):
        plan = services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        assert "frames" in plan
        services.run_decode(uart_state, None, None)
        alias = "cap"
        assert {k.split("|")[1] for k in uart_state.reports} == {"uart", "frames"}
        proto_events = [e for e in uart_state.reports[f"{alias}|uart"].events
                        if e.kind == "uart.frame"]
        pipe_events = uart_state.reports[f"{alias}|frames"].events
        assert all(e.kind == "uart.frame" for e in pipe_events)
        assert len(pipe_events) == len(proto_events)
        assert bytes(values_of(proto_events)) == PAYLOAD

    def test_report_node_is_graph_sink(self, uart_state):
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        lock = uart_state.locks["cap|frames"]
        has_out = {e.src for e in lock.graph.edges}
        sinks = [nid for nid in lock.graph.nodes if nid not in has_out]
        assert sinks == ["pipe0_event_filter"]

    def test_chain_on_chain(self, uart_state):
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        services.bind_pipeline(uart_state, "errors", "frames",
                               [{"type": "event_filter", "params": {"has_errors": True}}])
        services.run_decode(uart_state, None, None)
        assert "cap|errors" in uart_state.reports

    def test_tap_resolution_forms(self, uart_state):
        services.bind_pipeline(uart_state, "a", None, CHAIN)          # 缺省：唯一锁
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        services.bind_pipeline(uart_state, "b", "uart", CHAIN)        # 协议名
        services.bind_pipeline(uart_state, "c", "cap|frames", CHAIN)  # 完整键（tap 管线）

    def test_empty_chain_rejected(self, uart_state):
        with pytest.raises(ProtocolLockError, match="chain 不能为空"):
            services.bind_pipeline(uart_state, "x", "uart", [])

    def test_unknown_node_type(self, uart_state):
        with pytest.raises(ProtocolLockError, match="未注册"):
            services.bind_pipeline(uart_state, "x", "uart",
                                   [{"type": "nope_node"}])

    def test_port_type_mismatch(self, uart_state):
        """端口类型严格相等：events 输出接 capture 输入的节点即报错。"""
        with pytest.raises(ProtocolLockError, match="不匹配"):
            services.bind_pipeline(uart_state, "x", "uart",
                                   [{"type": "digital_pick"}])

    def test_duplicate_name_rejected(self, uart_state):
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        with pytest.raises(ProtocolLockError, match="已被协议/管线占用"):
            services.bind_pipeline(uart_state, "frames", "uart", CHAIN)

    def test_bad_tap(self, uart_state):
        with pytest.raises(ProtocolLockError, match="无法唯一定位"):
            services.bind_pipeline(uart_state, "x", "nope", CHAIN)

    def test_overrides_on_pipeline_rejected(self, uart_state):
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        with pytest.raises(ProtocolLockError, match="管线不支持 overrides"):
            services.run_decode(uart_state, {"baud": 9600}, "cap|frames")

    def test_flat_step_shorthand(self, uart_state):
        """扁写（参数平铺在 type 旁）与嵌套 params 等价（ADR-020）。"""
        services.bind_pipeline(uart_state, "flat", "uart",
                               [{"type": "event_filter", "kinds": ["uart.frame"]}])
        services.bind_pipeline(uart_state, "nested", "uart",
                               [{"type": "event_filter", "params": {"kinds": ["uart.frame"]}}])
        services.run_decode(uart_state, None, None)
        n_flat = uart_state.reports["cap|flat"].counts()["total"]
        n_nested = uart_state.reports["cap|nested"].counts()["total"]
        assert n_flat == n_nested > 0

    def test_flat_and_params_mixed_rejected(self, uart_state):
        with pytest.raises(ProtocolLockError, match="混用"):
            services.bind_pipeline(uart_state, "x", "uart",
                                   [{"type": "event_filter", "kinds": ["uart.frame"],
                                     "params": {"kinds": []}}])


class TestSinkSemantics:
    def test_filter_export_render_per_pipeline(self, uart_state, tmp_path):
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        services.run_decode(uart_state, None, None)
        ev = services.filter_events(uart_state, "cap", "frames", None, None, None, None)
        assert ev and all(e.kind == "uart.frame" for e in ev)
        p = services.export_events(uart_state, "csv", str(tmp_path / "f.csv"),
                                   source="cap", protocol="frames")
        assert p.is_file() and "uart.frame" in p.read_text(encoding="utf-8")
        png, table = services.render_timing(uart_state, None, None, 60, 150,
                                            source="cap", protocol="frames")
        assert str(png).endswith(".png")

    def test_save_profile_excludes_pipelines(self, uart_state, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        services.bind_pipeline(uart_state, "frames", "uart", CHAIN)
        services.save_profile(uart_state, "p1", "")
        import json
        data = json.loads((tmp_path / "profiles" / "p1.json").read_text(encoding="utf-8"))
        assert len(data["locks"]) == 1 and data["locks"][0]["protocol"] == "uart"

    def test_tool_visible_and_callable(self, uart_state):
        """网关工具：bind_pipeline 在 READY 可见且可调（ADR-020）。"""
        from decodehub.mcp_server.tools import TOOLS_BY_NAME, visible
        spec = TOOLS_BY_NAME["bind_pipeline"]
        assert spec.stage.value == "READY"
        assert visible(uart_state.stage, spec.stage)
        out = spec.handler({"name": "frames", "tap": "uart", "chain": CHAIN}, uart_state)
        assert "管线已绑定" in out[0]


class TestTapFanInLock:
    """锚扇入锁（双根）也可被 tap——注入点沿用上游锁的 source_inputs（ADR-020 修复）。"""

    def _downlink_state(self):
        import random

        from decodehub.acquisition.project import Project, SourceEntry
        from decodehub.decode.synth import encode_downlink, encode_uplink
        from decodehub.shared.waves import Capture, CaptureMeta

        rng = random.Random(2026)
        period, sym = 1.0 / 60.0, 31 * 1e-6
        frames = [tuple(rng.randrange(2) for _ in range(5)) for _ in range(2)]
        ul = encode_uplink([(0, 1, 0, 1, 0)] + frames, fs=10e6, period_s=period, seed=5)
        anchors = [0.37e-6 + (f + 1) * period + 0.5 * sym for f in range(-1, 2)]
        truth = [[tuple(rng.randrange(2) for _ in range(16)) for _ in range(5)]
                 + [(0,) * 16] for _ in anchors]
        dl = encode_downlink(anchors, truth, fs=10e6, delta_s=850e-6, seed=6)

        def cap(ch, cid):
            return Capture(meta=CaptureMeta(source_kind="synth", format_key="synth"),
                           analog=[ch], capture_id=cid)

        s = SessionState()
        s.project = Project()
        s.project.add(SourceEntry(alias="ch1", capture=cap(ul, "ch1")))
        s.project.add(SourceEntry(alias="ch2", capture=cap(dl, "ch2")))
        services.lock_protocol(s, "uplink", {}, source="ch1")
        services.lock_protocol(s, "downlink", {"uplink_source": "ch1"}, source="ch2")
        return s

    def test_tap_downlink_multi_root(self):
        s = self._downlink_state()
        plan = services.bind_pipeline(s, "packets", "ch2|downlink",
                                      [{"type": "event_filter", "kinds": ["downlink.packet"]}])
        assert "已绑定" in plan
        services.run_decode(s, None, None)
        r = s.reports["ch2|packets"]
        assert r.counts()["total"] > 0 and all(e.kind == "downlink.packet" for e in r.events)
        # 克隆图保留两个源注入点（上行锚子图 + 本源），报告节点仍是链尾
        assert sorted(s.locks["ch2|packets"].source_inputs.values()) == ["ch1", "ch2"]
