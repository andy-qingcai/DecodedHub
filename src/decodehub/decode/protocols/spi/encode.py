"""SPI 合成编码器（往返测试的编码方向；原理见同目录 README.md）。"""

from __future__ import annotations

import numpy as np

from ....shared.waves import DigitalWave


def encode_spi(
    words,
    freq: float = 1e6,
    cpol: int = 0,
    cpha: int = 0,
    word_bits: int = 8,
    bit_order: str = "msb",
    cs_words: list[int] | None = None,
    with_miso: bool = False,
) -> DigitalWave:
    """SPI 波形合成（通道 CLK, MOSI[, MISO], CS）。

    words: int 序列（MOSI）或 (mosi, miso) 元组序列。
    cs_words: 每次 CS 断言包含的词数（缺省 1 词/CS）。
    """
    CLK_BIT, MOSI_BIT, MISO_BIT, CS_BIT = 0, 1, 2, 3
    bit_t = 1.0 / freq
    half = bit_t / 2
    t = 4 * bit_t
    snaps: list[tuple[float, dict[str, int]]] = []
    cur = {"CLK": cpol, "MOSI": 0, "MISO": 0, "CS": 1}

    def set_(dt: float, **kw) -> float:
        nonlocal t
        t += dt
        cur.update(kw)
        snaps.append((t, dict(cur)))
        return t

    norm = []
    for w in words:
        if isinstance(w, tuple):
            norm.append(w)
        else:
            norm.append((w, w ^ 0x5A))

    groups: list[list[tuple[int, int]]] = []
    if cs_words:
        i = 0
        for n in cs_words:
            groups.append(norm[i : i + n])
            i += n
    else:
        groups = [[w] for w in norm]

    for g in groups:
        set_(half, CS=0)
        for (mv, sv) in g:
            for k in range(word_bits):
                pos = (word_bits - 1 - k) if bit_order == "msb" else k
                mb = (mv >> pos) & 1
                sb = (sv >> pos) & 1
                if cpha == 0:
                    set_(half, MOSI=mb, **({"MISO": sb} if with_miso else {}))
                    set_(half, CLK=1 - cpol)   # 前导沿（采样）
                    set_(half, CLK=cpol)       # 后导沿
                else:
                    set_(0, CLK=1 - cpol, MOSI=mb, **({"MISO": sb} if with_miso else {}))
                    set_(half, CLK=cpol)       # 后导沿（采样）
                    set_(half)                 # 回到空闲前的小间隙
        set_(half, CS=1)
        t += 2 * bit_t

    names_all = ["CLK", "MOSI", "MISO", "CS"]
    segs = []
    for tt, st in snaps:
        mask = ((st["CLK"] << CLK_BIT) | (st["MOSI"] << MOSI_BIT)
                | (st["MISO"] << MISO_BIT) | (st["CS"] << CS_BIT))
        segs.append((tt, mask))
    initial = (cpol << CLK_BIT) | (1 << CS_BIT)
    full = DigitalWave.from_segments(tuple(names_all), initial, segs, t_end=t)
    keep = ["CLK", "MOSI", "CS"] if not with_miso else names_all
    return full.select(keep)
