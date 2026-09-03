"""信号内核单测（DigitalWave IR 操作 / AnalogChannel 紧凑时间轴）。"""

import numpy as np
import pytest

from decodehub.shared import AnalogChannel, DigitalWave


def _two_ch_wave():
    # CH_A: 1→0→1；CH_B: 0→1
    return DigitalWave.from_segments(
        ["A", "B"], initial=0b01,
        segments=[(1.0, 0b00), (2.0, 0b10), (3.0, 0b11)],
        t_end=4.0, sample_rate=10.0, n_samples=40,
    )


class TestDigitalWave:
    def test_initial_and_edges(self):
        w = _two_ch_wave()
        assert w.initial == 0b01
        assert list(w.edges_t) == [1.0, 2.0, 3.0]
        assert list(w.edges_levels) == [0, 2, 3]

    def test_edge_stream_per_channel(self):
        w = _two_ch_wave()
        t, lv = w.edge_stream("A")
        assert list(t) == [1.0, 3.0] and list(lv) == [0, 1]
        t, lv = w.edge_stream("B")
        assert list(t) == [2.0] and list(lv) == [1]

    def test_level_at_freeze_extrapolation(self):
        w = _two_ch_wave()
        assert w.level_at("A", 0.5) == 1   # initial
        assert w.level_at("A", 1.5) == 0
        assert w.level_at("A", 1.0) == 0   # 跳变时刻取跳变后电平
        assert w.level_at("A", 9.9) == 1   # 冻结外推

    def test_select_remasks_and_prunes(self):
        w = _two_ch_wave()
        sub = w.select(["B"])
        assert sub.channels == ("B",)
        assert sub.initial == 0
        # 只有 B 变化处保留边
        assert list(sub.edges_t) == [2.0]
        assert list(sub.edges_levels) == [1]

    def test_select_missing_raises(self):
        w = _two_ch_wave()
        with pytest.raises(KeyError):
            w.select(["C"])

    def test_bool_roundtrip(self):
        fs = 1000.0
        arr = np.array([1, 1, 0, 0, 0, 1, 1, 1, 1, 0], dtype=np.uint8)
        w = DigitalWave.from_bool_array(arr, "X", fs)
        back = w.to_bool_array("X").astype(int)
        assert list(back) == list(arr)

    def test_strictly_increasing_enforced(self):
        with pytest.raises(ValueError):
            DigitalWave(("A",), 0, 0.0, np.array([1.0, 1.0]), np.array([1, 0], dtype=np.uint32), 2.0)

    def test_max_32_channels(self):
        with pytest.raises(ValueError):
            DigitalWave(tuple(f"c{i}" for i in range(33)), 0, 0.0, np.array([]), np.array([], dtype=np.uint32), 1.0)


class TestAnalogChannel:
    def test_compact_times(self):
        ch = AnalogChannel(name="CH1", samples=np.zeros(10, dtype=np.float32), t0=-1.0, dt=0.5)
        assert ch.n == 10
        assert ch.duration == 4.5
        assert ch.time_at(0) == -1.0 and ch.time_at(9) == 3.5

    def test_requires_dt_or_times(self):
        with pytest.raises(ValueError):
            AnalogChannel(name="bad", samples=np.zeros(4))
