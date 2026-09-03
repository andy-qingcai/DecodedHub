"""DOWNLINK 合成编码器（往返测试的编码方向；原理见同目录 README.md）。"""

from __future__ import annotations

import numpy as np

from ....shared.waves import AnalogChannel


def encode_downlink(
    frame_starts,
    packets_per_frame,
    fs: float,
    fc: float = 263e3,
    delta_s: float = 170e-6,
    slot_period: float | None = None,
    cycles_per_bit: int = 10,
    snr_db: float | None = None,
    seed: int | None = None,
) -> "AnalogChannel":
    """下行 DBPSK 合成（以上行帧起点为锚的包序列 → 模拟通道）。

    packets_per_frame: 每上行帧的包列表（每包 = 16 数据位元组；1 起始位自动加）。
    delta_s: 包锚点相对上行帧起点的偏移（接收端自校准，不配置）。
    slot_period: 槽间距（缺省 1/60/6）。
    """
    rng = np.random.default_rng(seed)
    n_sym = 1 + len(packets_per_frame[0][0])
    if slot_period is None:
        slot_period = (1.0 / 60.0) / max(1, len(packets_per_frame[0]))
    dur = max(frame_starts) + (len(packets_per_frame[0]) + 1) * slot_period
    n = int(dur * fs) + 10
    y = np.zeros(n)
    for t0_frame, bits6 in zip(frame_starts, packets_per_frame):
        for k, bits in enumerate(bits6):
            t_start = t0_frame + delta_s + k * slot_period
            i0 = int(round(t_start * fs))
            ncyc = n_sym * cycles_per_bit
            i1 = min(n, i0 + int(ncyc / fc * fs) + 2)
            if i0 < 0 or i1 <= i0:
                continue
            t = np.arange(i1 - i0) / fs
            off = np.zeros(ncyc)
            acc = 0.0
            for m in range(n_sym):
                off[m * cycles_per_bit:(m + 1) * cycles_per_bit] = acc
                if m + 1 < n_sym and bits[m]:
                    acc += 0.5
            n_samples = min(i1 - i0, int(ncyc / fc * fs) + 2)
            ts = t[:n_samples]
            cyc_idx = np.clip((ts * fc).astype(int), 0, ncyc - 1)
            sq = np.sign(np.sin(2 * np.pi * (ts * fc + off[cyc_idx])))
            y[i0:i0 + len(sq)] += sq[:len(y) - i0]
    if snr_db is not None:
        sig_power = float(np.mean(np.square(y - y.mean())))
        noise_power = sig_power / 10.0 ** (snr_db / 10.0)
        y = y + rng.normal(0.0, np.sqrt(noise_power), size=y.shape)
    return AnalogChannel(name="CH2", samples=y.astype(np.float32), units="V",
                         t0=0.0, dt=1.0 / fs)
