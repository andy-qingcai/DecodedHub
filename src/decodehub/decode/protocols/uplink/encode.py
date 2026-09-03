"""UPLINK 合成编码器（往返测试的编码方向；原理见同目录 README.md）。"""

from __future__ import annotations

import numpy as np

from ....shared.waves import AnalogChannel
from ..uplink.dsss import UplinkConfig


def encode_uplink(
    frames_data,
    fs: float,
    ppm: float = 0.0,
    snr_db: float | None = None,
    amp: float = 1.0,
    dc: float = 0.0,
    seed: int | None = None,
    period_s: float | None = 16.67e-3,
    env_hz: float = 60.0,
    env_amp: float = 0.0,
    chip_s: float = 1e-6,
    unipolar: bool = False,
    pn_word: int | None = None,
    pn_len: int | None = None,
    pream: tuple | list | None = None,
    data_bits_n: int | None = None,
) -> "AnalogChannel":
    """上行 DSSS 合成（帧数据 → PN 扩频 NRZ → 模拟通道）。

    frames_data: 数据位元组序列（长度 = 配置 n_data_bits）；period_s=None 连续模式，
    否则 60Hz 突发模式。ppm: 发端时钟偏差（真实信道实测约 −30800ppm）。
    pn_word/pn_len/pream/data_bits_n: 协议形状覆写（与解码参数对应，ADR-011）。
    返回 AnalogChannel（供 apick→uplink_precond→uplink_decode 全链路）。
    """
    from .dsss import UplinkConfig

    over: dict = {"chip_s": chip_s,
                  "chip_mapping": "unipolar" if unipolar else "bipolar"}
    if pn_word is not None:
        over["pn_word"] = pn_word
    if pn_len is not None:
        over["pn_len"] = pn_len
    if pream is not None:
        over["pream_bits"] = tuple(pream)
    if data_bits_n is not None:
        over["n_data_bits"] = data_bits_n
    cfg = UplinkConfig(**over)
    rng = np.random.default_rng(seed)
    bits: list[int] = []
    for data in frames_data:
        assert len(data) == cfg.n_data_bits
        bits.extend(cfg.pream_bits)
        bits.extend(int(b) & 1 for b in data)
    chips = np.concatenate([
        cfg.pn_bits if bit else 1 - cfg.pn_bits for bit in bits
    ]).astype(np.float64)

    t0 = 0.37 * chip_s
    chip_t = (1.0 + ppm * 1e-6) * chip_s
    if period_s is None:
        starts = np.arange(len(chips) // cfg.pn_len) * cfg.symbol_s
        total_chips = len(chips)
    else:
        starts = np.array([f * period_s for f in range(len(frames_data))],
                          dtype=np.float64)
        total_chips = int(np.ceil(
            (starts[-1] + period_s + cfg.frame_s) / chip_t)) + 8

    n = int(np.ceil((t0 + total_chips * chip_t + 2.0 / fs) * fs))
    t = np.arange(n) / fs
    if period_s is None:
        k = np.floor((t - t0) / chip_t).astype(np.int64)
        np.clip(k, 0, len(chips) - 1, out=k)
        level, mask = chips[k], np.ones_like(t, dtype=bool)
    else:
        level = np.zeros_like(t)
        mask = np.zeros(len(t), dtype=bool)
        for f, s in enumerate(starts):
            i0 = int((t0 + s) * fs)
            i1 = min(len(t), int((t0 + s + cfg.frame_s) * fs))
            kk = np.floor((t[i0:i1] - t0 - s) / chip_t).astype(np.int64)
            np.clip(kk, 0, cfg.n_symbols * cfg.pn_len - 1, out=kk)
            level[i0:i1] = chips[f * cfg.n_symbols * cfg.pn_len + kk]
            mask[i0:i1] = True
    if cfg.chip_mapping == "bipolar":
        y = amp * (2.0 * level - 1.0) * mask
    else:
        y = amp * level * mask
    y = y + dc
    if env_amp:
        y = y + env_amp * np.sin(2 * np.pi * env_hz * t + rng.uniform(0, 2 * np.pi))
    if snr_db is not None:
        sig_power = float(np.mean(np.square(y - y.mean())))
        noise_power = sig_power / 10.0 ** (snr_db / 10.0)
        y = y + rng.normal(0.0, np.sqrt(noise_power), size=y.shape)
    return AnalogChannel(name="CH1", samples=y.astype(np.float32), units="V",
                         t0=0.0, dt=1.0 / fs)
