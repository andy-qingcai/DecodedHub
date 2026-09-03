"""I2C / SPI 往返属性测试（矩阵 I1–I6 / S1–S8，docs/60-testing.md）。"""

import pytest

from decodehub.decode.synth import encode_i2c, encode_spi
from tests.conftest import run_i2c, run_spi, transfers_of


def _transfers(events):
    return transfers_of(events)


class TestI2cRoundTrip:
    def test_i1_basic_write(self):
        for freq in (100e3, 400e3):
            wave = encode_i2c([
                {"addr": 0x51, "read": False, "data": [0x12, 0x34]},
            ], freq=freq)
            ev = run_i2c(wave)
            trs = _transfers(ev)
            assert len(trs) == 1
            t = trs[0]
            assert t.address == 0x51 and t.read is False
            assert t.data_bytes == [0x12, 0x34]
            assert t.acks == [True, True, True]
            assert not t.errors

    def test_i1_multi_transaction(self):
        wave = encode_i2c([
            {"addr": 0x1A, "read": False, "data": [0x00]},
            {"addr": 0x2B, "read": True, "data": [0xDE, 0xAD]},
        ])
        trs = _transfers(run_i2c(wave))
        assert [(t.address, t.read) for t in trs] == [(0x1A, False), (0x2B, True)]
        assert trs[1].data_bytes == [0xDE, 0xAD]

    def test_i2_read_with_final_nack(self):
        wave = encode_i2c([
            {"addr": 0x50, "read": True, "data": [0x01, 0x02], "final_nack": True},
        ])
        trs = _transfers(run_i2c(wave))
        assert trs[0].acks == [True, True, False]
        assert "nack" in trs[0].errors
        # 逐字节事件中 NACK 标注
        data_events = [e for e in run_i2c(wave) if e.kind == "i2c.data"]
        assert data_events[-1].ann_class == "warn"

    def test_i3_repeated_start(self):
        wave = encode_i2c([
            {"addr": 0x48, "read": False, "data": [0x10], "repeat_next": True},
            {"addr": 0x48, "read": True, "data": [0xAB]},
        ])
        ev = run_i2c(wave)
        assert any(e.kind == "i2c.repeat-start" for e in ev)
        # 重复起始合并为同一传输（START..STOP 语义）；方向取最后一次 START
        assert len(_transfers(ev)) == 1
        t = _transfers(ev)[0]
        assert t.data_bytes == [0x10, 0xAB]
        assert t.read is True  # 写寄存器后读出的典型组合事务

    def test_i5_clock_stretch(self):
        wave = encode_i2c(
            [{"addr": 0x21, "read": False, "data": [0x55]}],
            freq=100e3, stretch_s=5e-3,
        )
        ev = run_i2c(wave)  # 默认 stretch_warn_s=1ms → 应告警
        assert any(e.errors == ["clock-stretch"] for e in ev)
        t = _transfers(ev)[0]
        assert t.data_bytes == [0x55]  # 解码不受影响

    def test_i5_clock_stretch_silent_when_under_threshold(self):
        wave = encode_i2c(
            [{"addr": 0x21, "read": False, "data": [0x55]}],
            freq=100e3, stretch_s=5e-3,
        )
        ev = run_i2c(wave, stretch_warn_s=0.05)
        assert not any("clock-stretch" in e.errors for e in ev)

    def test_i4_10bit_address(self):
        # 手工构造 10-bit：首字节 11110 + A9A8(0b10→bit2:1,bit1:0? 用 0xF4 → A9A8=10)
        # 直接用位级构造太繁 → 用组合：首字节 0xF4 (11110 0 0 W? 0xF4=11110100: A9A8=10, RW=0)
        # 第二字节 A7..A0 = 0x35 → 地址 = (0b10 << 8) | 0x35 = 0x235
        from decodehub.decode.synth import encode_i2c as enc
        # 借用低层：写一个只含地址的事务，然后手工替换 first_byte 不可行——
        # 改为直接构造波形（10-bit 序列）：
        bit_t = 1 / 100e3
        half = bit_t / 2

        def push_byte(snaps, t, b, ack):
            for k in range(8):
                bit = (b >> (7 - k)) & 1
                t += half; snaps.append((t, 0, bit))
                t += half; snaps.append((t, 1, bit))
            t += half; snaps.append((t, 0, 0 if ack else 1))
            t += half; snaps.append((t, 1, 0 if ack else 1))
            return t

        snaps = []
        t = 10 * bit_t
        snaps.append((t, 1, 1))
        t += half; snaps.append((t, 1, 0))  # START
        t += half; snaps.append((t, 0, 0))
        t = push_byte(snaps, t, 0xF4, True)   # 11110 10 W
        t = push_byte(snaps, t, 0x35, True)   # A7..A0
        # STOP
        t += half; snaps.append((t, 0, 0))
        t += half; snaps.append((t, 1, 0))
        t += half; snaps.append((t, 1, 1))
        from decodehub.shared import DigitalWave
        wave = DigitalWave.from_segments(
            ["SCL", "SDA"], 0b11,
            [(tt, (s << 0) | (d << 1)) for tt, s, d in snaps],
            t_end=t + 10 * bit_t,
        )
        ev = run_i2c(wave)
        trs = _transfers(ev)
        assert trs[0].address == 0x235 and trs[0].is_10bit
        assert trs[0].data_bytes == []

    def test_time_ordered(self):
        wave = encode_i2c([
            {"addr": 0x10, "read": False, "data": list(range(8))},
            {"addr": 0x20, "read": True, "data": [0xFF]},
        ])
        ev = run_i2c(wave)
        ts = [e.t_start for e in ev]
        assert ts == sorted(ts)


class TestSpiRoundTrip:
    WORDS = [0xA5, 0x3C, 0x00, 0xFF, 0x12]

    @pytest.mark.parametrize("cpol,cpha", [(0, 0), (0, 1), (1, 0), (1, 1)])
    def test_s1_s4_mode_matrix(self, cpol, cpha):
        wave = encode_spi(self.WORDS, freq=1e6, cpol=cpol, cpha=cpha)
        ev = run_spi(wave, cpol=cpol, cpha=cpha)
        words = [e.mosi for e in ev if e.kind == "spi.word"]
        assert words == self.WORDS

    def test_s5_burst_multi_word_cs(self):
        wave = encode_spi(self.WORDS, freq=1e6, cs_words=[len(self.WORDS)])
        ev = run_spi(wave)
        trs = [e for e in ev if e.kind == "spi.transfer"]
        assert len(trs) == 1
        assert [m for m, _ in trs[0].words] == self.WORDS

    def test_s5_word_per_cs(self):
        wave = encode_spi(self.WORDS, freq=1e6)
        ev = run_spi(wave)
        trs = [e for e in ev if e.kind == "spi.transfer"]
        assert len(trs) == len(self.WORDS)
        assert all(len(t.words) == 1 for t in trs)

    @pytest.mark.parametrize("word_bits,order", [(4, "msb"), (8, "msb"), (12, "msb"),
                                                 (8, "lsb"), (16, "msb"), (32, "msb")])
    def test_s6_word_sizes_and_order(self, word_bits, order):
        mask = (1 << word_bits) - 1
        words = [(i * 37 + 11) & mask for i in range(6)]
        wave = encode_spi(words, freq=500e3, word_bits=word_bits, bit_order=order)
        ev = run_spi(wave, word_bits=word_bits, bit_order=order)
        assert [e.mosi for e in ev if e.kind == "spi.word"] == words

    def test_s7_no_cs_degraded(self):
        wave = encode_spi(self.WORDS[:3], freq=1e6, cs_words=None)
        # 移除 CS 通道
        from decodehub.shared import DigitalWave
        w2 = DigitalWave(
            channels=("CLK", "MOSI"), initial=wave.initial & 0b011,
            t_start=wave.t_start,
            edges_t=wave.edges_t, edges_levels=wave.edges_levels & 0b011,
            t_end=wave.t_end,
        )
        w2 = w2.select(["CLK", "MOSI"])
        ev = run_spi(w2)
        assert any(e.errors == ["no-cs"] for e in ev)
        assert [e.mosi for e in ev if e.kind == "spi.word"] == self.WORDS[:3]

    def test_s8_cs_midword(self):
        # 2 词 CS 区间被截断在第二词中途：相对 CS 上升沿截断
        from decodehub.shared import DigitalWave
        wave = encode_spi([0xA5, 0x5A], freq=1e6, cs_words=[2])
        ev = run_spi(wave)
        assert [m for m, _ in [e for e in ev if e.kind == "spi.transfer"][0].words] == [0xA5, 0x5A]
        bit_t = 1e-6
        cs_t, cs_lv = wave.edge_stream("CS")
        t_off = float(cs_t[list(cs_lv).index(1)])  # CS 释放时刻
        cut_t = t_off - 1.5 * bit_t                 # 第二词中途（共 16 bit）
        m = wave.edges_t < cut_t
        cut = DigitalWave(channels=wave.channels, initial=wave.initial,
                          t_start=wave.t_start, edges_t=wave.edges_t[m],
                          edges_levels=wave.edges_levels[m], t_end=cut_t)
        ev2 = run_spi(cut)
        assert any(e.errors == ["cs-midword"] for e in ev2)

    def test_mosi_miso(self):
        wave = encode_spi([(0x0F, 0xF0), (0x33, 0xCC)], freq=1e6, with_miso=True)
        ev = run_spi(wave)
        words = [(e.mosi, e.miso) for e in ev if e.kind == "spi.word"]
        assert words == [(0x0F, 0xF0), (0x33, 0xCC)]

    def test_cs_active_high(self):
        wave = encode_spi(self.WORDS[:2], freq=1e6)
        from decodehub.shared import DigitalWave
        # 反相 CS
        lv = wave.edges_levels.copy()
        b = wave.channels.index("CS")
        initial = wave.initial ^ (1 << b)
        lv = lv ^ np_uint32(1 << b) if False else lv
        import numpy as np
        lv = lv.astype(np.uint32) ^ np.uint32(1 << b)
        w2 = DigitalWave(channels=wave.channels, initial=initial, t_start=wave.t_start,
                         edges_t=wave.edges_t, edges_levels=lv, t_end=wave.t_end)
        ev = run_spi(w2, cs_active="high")
        assert [e.mosi for e in ev if e.kind == "spi.word"] == self.WORDS[:2]


def np_uint32(x):
    import numpy as np

    return np.uint32(x)
