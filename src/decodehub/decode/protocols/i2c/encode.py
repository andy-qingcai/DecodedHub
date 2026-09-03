"""I2C 合成编码器（往返测试的编码方向；原理见同目录 README.md）。"""

from __future__ import annotations

import numpy as np

from ....shared.waves import DigitalWave


def encode_i2c(
    transactions: list[dict],
    freq: float = 100e3,
    stretch_s: float = 0.0,
    gap_s: float = 5e-5,
) -> DigitalWave:
    """I2C 波形合成（通道 SCL, SDA）。

    transactions: [{addr, read, data: [int...], acks: [bool...]（缺省全 ACK）,
                    repeat: bool（Sr 起始，不先发 STOP）, final_nack: bool}]
    """
    bit_t = 1.0 / freq
    half = bit_t / 2
    t = gap_s
    SCL, SDA = 0, 1  # bit0=SCL, bit1=SDA
    snaps: list[tuple[float, int, int]] = []  # (t, scl, sda)
    cur = [1, 1]

    def set_(dt: float, scl: int | None = None, sda: int | None = None) -> float:
        nonlocal t
        t += dt
        if scl is not None:
            cur[0] = scl
        if sda is not None:
            cur[1] = sda
        snaps.append((t, cur[0], cur[1]))
        return t

    def byte(b: int, ack: bool, first_stretch: bool = False) -> None:
        for k in range(8):
            bit = (b >> (7 - k)) & 1
            set_(half, 0, bit)           # SCL 低半周，SDA 置位
            extra = stretch_s if (first_stretch and k == 0) else 0.0
            set_(half + extra, 1)        # SCL 高半周（上升沿采样 SDA=bit）
        set_(half, 0, 0 if ack else 1)   # ACK 槽
        set_(half, 1)

    for idx, tr in enumerate(transactions):
        addr = tr["addr"] & 0x7F
        first_byte = (addr << 1) | (1 if tr.get("read") else 0)
        data = tr.get("data", [])
        acks = tr.get("acks", [True] * (1 + len(data)))
        if tr.get("final_nack") and acks:
            acks = list(acks)
            acks[-1] = False
        if idx > 0 and transactions[idx - 1].get("repeat_next"):
            # 重复 START：SCL 低时释放 SDA 高 → SCL 高 → SDA 下降（Sr）
            set_(half, 0, 1)
            set_(half, 1, 1)
            set_(half, 1, 0)
        else:
            if idx > 0:
                # 正常 STOP：SCL 低半周拉 SDA → SCL 高 → SDA 上升
                set_(half, 0, 0)
                set_(half, 1, 0)
                set_(half, 1, 1)
                t += gap_s
            # START：SCL 高时 SDA 下降
            set_(half, 1, 1)
            set_(half, 1, 0)
        set_(half, 0, 0)  # SCL 降至低，进入首字节
        byte(first_byte, acks[0], first_stretch=(stretch_s > 0 and idx == 0))
        for j, d in enumerate(data):
            byte(d, acks[1 + j] if 1 + j < len(acks) else True)

    # 收尾 STOP（SCL 已高：ack 槽结束）
    set_(half, 0, 0)
    set_(half, 1, 0)
    set_(half, 1, 1)
    t += gap_s

    segs = [(tt, (s << SCL) | (d << SDA)) for tt, s, d in snaps]
    wave = DigitalWave.from_segments(["SCL", "SDA"], 0b11, segs, t_end=t)
    return wave
