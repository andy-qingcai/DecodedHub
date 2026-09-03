"""多源工程（Project）单测：合并语义 / 偏移 / 墙钟对齐 / 混合解码（ADR-008）。

核心价值验证：SCL 来自源 A、SDA 来自源 B（不同采集器），对齐合并后同一 I2C 解码器解出。
"""

import numpy as np
import pytest

from decodehub.acquisition.project import Project, SourceEntry
from decodehub.decode.synth import encode_i2c, encode_uart
from decodehub.shared import Capture, CaptureMeta
from decodehub.shared.waves import AnalogChannel
from tests.conftest import run_i2c, transfers_of


def _cap(wave=None, analog=None, cid="cap"):
    return Capture(
        meta=CaptureMeta(source_kind="synth", format_key="synth", source_files=[cid]),
        digital=wave, analog=analog or [], capture_id=cid,
    )


class TestProjectMerge:
    def test_single_source_backward_compatible(self):
        """单源：不加命名空间、不加偏移时返回原对象。"""
        w = encode_uart(b"\x41", baud=9600)
        p = Project()
        p.add(SourceEntry("la", _cap(w, cid="la-1")))
        m = p.merged()
        assert m.digital.channels == ("TX",)  # 无前缀
        assert m.capture_id == "la-1"

    def test_single_source_with_offset_shifts(self):
        w = encode_uart(b"\x41", baud=9600)
        p = Project()
        p.add(SourceEntry("la", _cap(w, cid="la-1"), offset=0.5))
        m = p.merged()
        assert m.digital.t_start == pytest.approx(w.t_start + 0.5)
        assert m.digital.edges_t[0] == pytest.approx(w.edges_t[0] + 0.5)

    def test_two_sources_namespace_and_timeline(self):
        a = encode_uart(b"\x11", baud=9600)                       # t: 0 .. ~2.6ms
        b = encode_uart(b"\x22", baud=9600)                       # 同
        p = Project()
        p.add(SourceEntry("la", _cap(a, cid="a")))
        p.add(SourceEntry("scope", _cap(b, cid="b"), offset=1e-3))
        m = p.merged()
        assert m.digital.channels == ("la:TX", "scope:TX")
        assert m.digital.t_start == pytest.approx(0.0)            # la 起点
        assert m.digital.t_end == pytest.approx(1e-3 + b.t_end)   # 平移后的 scope 终点
        # la:TX 跳变流与原始一致；scope:TX 平移 1ms
        t, _lv = m.digital.edge_stream("la:TX")
        t0, _ = a.edge_stream("TX")
        assert list(t) == list(t0)
        t, _lv = m.digital.edge_stream("scope:TX")
        t1, _ = b.edge_stream("TX")
        assert list(t) == [x + 1e-3 for x in t1]

    def test_set_offsets_incremental_and_invalidation(self):
        a = encode_uart(b"\x11", baud=9600)
        b = encode_uart(b"\x22", baud=9600)
        p = Project()
        p.add(SourceEntry("la", _cap(a, cid="a")))
        p.add(SourceEntry("scope", _cap(b, cid="b")))
        before = p.merged()
        p.set_offsets({"scope": 2e-3})  # 未提及 la → 保持 0
        after = p.merged()
        assert before is not after
        assert p.find("la").offset == 0.0
        assert p.find("scope").offset == 2e-3
        # 相同 key 再次 merged → 命中缓存（同一对象）
        assert p.merged() is after

    def test_analog_shifted(self):
        ch = AnalogChannel(name="CH1", samples=np.linspace(0, 1, 10, dtype=np.float32),
                           t0=0.0, dt=0.1)
        p = Project()
        p.add(SourceEntry("la", _cap(encode_uart(b"\x01", baud=9600), cid="a")))
        p.add(SourceEntry("adc", _cap(None, analog=[ch], cid="c"), offset=0.25))
        m = p.merged()
        assert m.analog[0].t0 == pytest.approx(0.25)
        assert m.analog[0].name == "adc:CH1"

    def test_channel_limit_32(self):
        from decodehub.shared.waves import DigitalWave

        def wide(n):
            return DigitalWave(
                channels=tuple(f"c{i}" for i in range(n)), initial=0, t_start=0.0,
                edges_t=np.array([]), edges_levels=np.array([], dtype=np.uint32), t_end=1.0)
        p = Project()
        p.add(SourceEntry("a", _cap(wide(20), cid="a")))
        p.add(SourceEntry("b", _cap(wide(20), cid="b")))
        with pytest.raises(ValueError, match="32"):
            p.merged()

    def test_duplicate_alias_rejected(self):
        p = Project()
        p.add(SourceEntry("la", _cap(cid="a")))
        with pytest.raises(ValueError, match="重复"):
            p.add(SourceEntry("la", _cap(cid="b")))


class TestWallclockAlignment:
    def test_align_by_wallclock(self):
        from datetime import datetime

        p = Project()
        p.add(SourceEntry("la", _cap(cid="a"), t_wall=datetime(2026, 9, 3, 10, 0, 0)))
        p.add(SourceEntry("scope", _cap(cid="b"),
                          t_wall=datetime(2026, 9, 3, 10, 0, 0, 250_000)))  # +250ms
        offsets = p.align_by_wallclock()
        assert offsets == {"la": 0.0, "scope": pytest.approx(0.25)}

    def test_missing_t_wall_errors(self):
        from datetime import datetime

        p = Project()
        p.add(SourceEntry("la", _cap(cid="a"), t_wall=datetime(2026, 9, 3, 10, 0, 0)))
        p.add(SourceEntry("scope", _cap(cid="b")))  # 无 t_wall
        with pytest.raises(ValueError, match="t_wall"):
            p.align_by_wallclock()


class TestCrossDeviceDecode:
    def test_i2c_scl_from_a_sda_from_b(self):
        """杀手级场景：SCL 来自源 A（LA），SDA 来自源 B（另一台设备），
        已知偏移对齐合并后，同一 I2C 解码器完整解出传输。"""
        full = encode_i2c([
            {"addr": 0x51, "read": False, "data": [0xDE, 0xAD]},
        ], freq=100e3)

        # 拆成两个单通道采集（模拟两台设备各采到一根线）
        off = 137e-6  # 设备 B 晚于设备 A 137µs 开始
        p = Project()
        p.add(SourceEntry("la", _cap(full.select(["SCL"]), cid="scl-only")))
        p.add(SourceEntry("scope", _cap(
            _shift_wave(full.select(["SDA"]), -off), cid="sda-only"), offset=off))
        merged = p.merged()
        assert merged.digital.channels == ("la:SCL", "scope:SDA")

        events = run_i2c(merged.digital, scl="la:SCL", sda="scope:SDA")
        trs = transfers_of(events)
        assert len(trs) == 1
        assert trs[0].address == 0x51
        assert trs[0].data_bytes == [0xDE, 0xAD]
        assert trs[0].acks == [True, True, True]
        assert not trs[0].errors

    def test_cross_device_via_csv_roundtrip(self, tmp_path):
        """回归：CSV 往返引入的浮点 ulp 噪声不得把"同时翻转"拆成两条边
        （曾导致 SCL/SDA 跨源合并后 I2C 误判大量伪 START）。"""
        from decodehub.decode.synth import save_kingst_csv
        from decodehub.acquisition import load_capture

        full = encode_i2c([{"addr": 0x37, "read": True, "data": [0x11, 0x22]}], freq=400e3)
        off = 55e-6
        f1 = tmp_path / "la.csv"
        f2 = tmp_path / "scope.csv"
        save_kingst_csv(full.select(["SCL"]), f1)
        save_kingst_csv(_shift_wave(full.select(["SDA"]), -off), f2)
        p = Project()
        p.add(SourceEntry("la", load_capture(f1)))
        p.add(SourceEntry("scope", load_capture(f2), offset=off))
        trs = transfers_of(run_i2c(p.merged().digital, scl="la:SCL", sda="scope:SDA"))
        assert len(trs) == 1
        assert trs[0].address == 0x37 and trs[0].read is True
        assert trs[0].data_bytes == [0x11, 0x22]
        assert not trs[0].errors

    def test_role_automap_with_namespace(self):
        """通道自动映射识别 `别名:` 前缀（按冒号后缀匹配）。"""
        from decodehub.decode.bindings import auto_map_channels, get_binding

        b = get_binding("i2c")
        chs = ["la:通道 0", "la:通道 1", "scope:CH1", "adc:电流"]
        m = auto_map_channels(chs, b, {})
        assert m == {"scl": "la:通道 0", "sda": "la:通道 1"}
        m2 = auto_map_channels(["x:SCL", "y:SDA"], b, {})
        assert m2 == {"scl": "x:SCL", "sda": "y:SDA"}


def _shift_wave(w, dt):
    from decodehub.shared.waves import DigitalWave

    return DigitalWave(
        channels=w.channels, initial=w.initial, t_start=w.t_start + dt,
        edges_t=w.edges_t + dt, edges_levels=w.edges_levels,
        t_end=w.t_end + dt, sample_rate=w.sample_rate, n_samples=w.n_samples,
    )
