"""UART 合成编码器（往返测试的编码方向；原理见同目录 README.md）。"""

from __future__ import annotations

import numpy as np

from ....shared.waves import DigitalWave


def encode_uart(
    data,
    baud: float,
    data_bits: int = 8,
    parity: str = "N",
    stop_bits: float = 1.0,
    invert: bool = False,
    bit_order: str = "lsb",
    idle_bits: float = 2.0,
    jitter_ui: float = 0.0,
    drift_ppm: float = 0.0,
    seed: int | None = None,
) -> DigitalWave:
    """字节流 → 单通道（TX）逻辑波形。电平为逻辑值（invert 时输出物理反相）。"""
    rng = np.random.default_rng(seed)
    bit_t = 1.0 / baud
    t = idle_bits * bit_t
    segs: list[tuple[float, int]] = []  # (t, 逻辑电平)
    level = 1
    for bi, byte in enumerate(bytes(data) if not isinstance(data, (list, tuple)) else data):
        bt = bit_t * (1.0 + drift_ppm * 1e-6 * bi)
        bits: list[int] = [(byte >> (k if bit_order == "lsb" else data_bits - 1 - k)) & 1
                           for k in range(data_bits)]
        ones = sum(bits)
        if parity == "E":
            bits.append(ones % 2)
        elif parity == "O":
            bits.append((ones + 1) % 2)
        bits.append(1)  # stop
        frame = [0] + bits  # start
        for lv in frame:
            if lv != level:
                segs.append((t, lv))
                level = lv
            t += bt
        t += idle_bits * bt
    t += bit_t  # 尾部余量

    times = np.array([s[0] for s in segs], dtype=np.float64)
    levels = np.array([s[1] for s in segs], dtype=np.uint8)
    if jitter_ui > 0:
        times = times + rng.uniform(-0.25, 0.25, times.size) * jitter_ui * bit_t
        times = np.maximum.accumulate(times)  # 保持单调
        keep = np.concatenate(([True], np.diff(times) > 1e-15))
        times, levels = times[keep], levels[keep]
    if invert:
        levels = 1 - levels
        init = 0
    else:
        init = 1
    return DigitalWave.from_segments(["TX"], init, list(zip(times, levels)), t_end=t)
