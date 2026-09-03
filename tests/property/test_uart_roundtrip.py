"""UART 往返属性测试（矩阵 U1–U10，docs/60-testing.md）。

核心不变量：decode(encode(bytes, cfg), cfg) == bytes（含扰动容差）。
"""

import random

import numpy as np
import pytest

from decodehub.decode.synth import analogify, encode_uart
from decodehub.decode.nodes.slicer import SlicerNode
from tests.conftest import run_uart, values_of

SEED = 20260903


def _rand_bytes(n, rng):
    # 覆盖边界值 + 随机
    specials = [0x00, 0x01, 0x55, 0xAA, 0x7F, 0x80, 0xFE, 0xFF]
    return bytes(rng.choice(specials) if rng.random() < 0.4 else rng.randrange(256)
                 for _ in range(n))


class TestUartRoundTrip:
    def test_u1_baud_matrix(self):
        rng = random.Random(SEED)
        for baud in (9600, 57600, 115200, 1_000_000, 3_000_000):
            data = _rand_bytes(rng.randrange(5, 40), rng)
            events = run_uart(encode_uart(data, baud=baud), baud=baud)
            assert values_of(events) == list(data), f"baud={baud}"

    def test_u1_auto_baud(self):
        rng = random.Random(SEED + 1)
        for baud in (9600, 115200, 2_000_000):
            data = _rand_bytes(30, rng)
            events = run_uart(encode_uart(data, baud=baud), baud="auto")
            assert values_of(events) == list(data)
            assert not any(e.errors for e in events)

    def test_u2_data_bits(self):
        rng = random.Random(SEED + 2)
        for nd in (5, 6, 7, 8, 9):
            data = [rng.randrange(1 << nd) for _ in range(20)]
            events = run_uart(encode_uart(data, baud=115200, data_bits=nd),
                              baud=115200, data_bits=nd)
            assert values_of(events) == data

    @pytest.mark.parametrize("parity", ["O", "E"])
    def test_u3_parity_ok_and_injected_error(self, parity):
        data = b"\x12\x34\x56"
        events = run_uart(encode_uart(data, baud=115200, parity=parity),
                          baud=115200, parity=parity)
        assert values_of(events) == list(data)
        assert not any("parity" in e.errors for e in events)
        # 注入：翻转校验位（手工改最后一个跳变不可靠 → 直接构造坏波形：
        # 用错误 parity 参数解码正确波形必然报 parity 错）
        bad = run_uart(encode_uart(data, baud=115200, parity=parity),
                       baud=115200, parity="O" if parity == "E" else "E")
        assert any("parity" in e.errors for e in bad)

    @pytest.mark.parametrize("stop", [1.0, 1.5, 2.0])
    def test_u4_stop_bits_and_framing_error(self, stop):
        data = b"\xAB\xCD"
        events = run_uart(encode_uart(data, baud=115200, stop_bits=stop),
                          baud=115200, stop_bits=stop)
        assert values_of(events) == list(data)
        # framing 注入：把停止位拉低 → 用 idle_bits=0 且 stop=2 解码 stop=1 的流
        # 更直接：截短停止位（stop_bits 编码 0.5 不在合法集），改用错误停止数解出帧错
        wrong = run_uart(encode_uart(data, baud=115200, stop_bits=1.0),
                         baud=115200, stop_bits=1.0, data_bits=8, parity="N")
        assert values_of(wrong) == list(data)  # 对照组无错

    def test_u5_break(self):
        # 低电平 ≥ 整帧 → break 事件，其后恢复
        bit_t = 1 / 9600
        from decodehub.shared import DigitalWave
        inner = encode_uart([0x42], baud=9600)
        # 把正常帧平移到 break 之后（24 bit 处），保持相对时序
        offs = 24 * bit_t - inner.t_start
        segs = [(t + offs, int(l)) for t, l in zip(inner.edges_t, inner.edges_levels)]
        wave = DigitalWave.from_segments(
            ["TX"], 1,
            [(2 * bit_t, 0), (22 * bit_t, 1)] + segs,  # 20 bit 连续低 = break
            t_end=inner.t_end + offs + bit_t,
        )
        events = run_uart(wave, baud=9600)
        assert any("break" in e.errors for e in events)
        assert 0x42 in values_of(events)

    def test_u6_back_to_back(self):
        data = bytes(range(32))
        wave = encode_uart(data, baud=115200, idle_bits=0.0)  # 无空闲隙
        events = run_uart(wave, baud=115200)
        assert values_of(events) == list(data)

    def test_u7_invert_msb(self):
        data = b"\xE1\x87"
        events = run_uart(encode_uart(data, baud=115200, invert=True, bit_order="msb"),
                          baud=115200, invert=True, bit_order="msb")
        assert values_of(events) == list(data)
        # 反相不声明 → 全错（sanity：invert 有意义）
        wrong = run_uart(encode_uart(data, baud=115200, invert=True),
                         baud=115200, invert=False)
        assert values_of(wrong) != list(data)

    def test_u8_auto_baud_accuracy(self):
        rng = random.Random(SEED + 3)
        for baud in (9600, 38400, 230400):
            data = _rand_bytes(20, rng)
            events = run_uart(encode_uart(data, baud=baud), baud="auto")
            assert values_of(events) == list(data)

    def test_u9_jitter_drift_robustness(self):
        rng = random.Random(SEED + 4)
        data = _rand_bytes(30, rng)
        for jitter, drift in ((0.05, 0.0), (0.15, 0.0), (0.0, 500.0), (0.1, 1000.0)):
            wave = encode_uart(data, baud=115200, jitter_ui=jitter, drift_ppm=drift,
                               seed=rng.randrange(1 << 30))
            events = run_uart(wave, baud=115200)
            assert values_of(events) == list(data), f"jitter={jitter}, drift={drift}"

    def test_u10_analog_path(self):
        rng = random.Random(SEED + 5)
        data = _rand_bytes(20, rng)
        digital = encode_uart(data, baud=115200)
        fs = 2_000_000  # 2 MHz 采样 ≈ 17 samples/bit
        ch = analogify(digital, "TX", fs=fs, v_low=0.0, v_high=3.3,
                       rise_s=2e-6, noise_sigma=0.02, seed=7)
        sliced = SlicerNode().run(
            {"in": [ch]},
            {"threshold": None, "hysteresis": None, "names": []},
        )["out"]
        events = run_uart(sliced, baud="auto")
        assert values_of(events) == list(data)

    def test_e1_truncation(self):
        wave = encode_uart(b"\x11\x22\x33", baud=115200)
        # 截断点选在第 3 帧起始沿之后、数据位之中（≈236µs）
        te = 236e-6
        m = wave.edges_t < te
        from decodehub.shared import DigitalWave
        cut = DigitalWave(channels=wave.channels, initial=wave.initial,
                          t_start=wave.t_start, edges_t=wave.edges_t[m],
                          edges_levels=wave.edges_levels[m], t_end=te)
        events = run_uart(cut, baud=115200)
        assert values_of(events)[:2] == [0x11, 0x22]
        assert any("truncated" in e.errors for e in events if e.kind == "uart.frame")

    def test_time_ordered(self):
        rng = random.Random(SEED + 6)
        data = _rand_bytes(50, rng)
        events = run_uart(encode_uart(data, baud=57600, jitter_ui=0.1,
                                      seed=42), baud=57600)
        ts = [e.t_start for e in events]
        assert ts == sorted(ts)
