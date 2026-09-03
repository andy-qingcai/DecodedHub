"""SPI 解码器（docs/41-decode.md §3.3）。

- 采样沿查表：模式 0/3 → 上升沿；模式 1/2 → 下降沿（(cpol==0)==(cpha==0)）；
- CS 帧化：CS 激活期 = 一个 transfer（含有序 words）；无 CS 通道时按位计数分词并 WARN；
- 词中 CS 翻转 → cs-midword 告警并复位该词。
"""

from __future__ import annotations

from typing import Any

from ....shared.waves import DigitalWave
from ...events import SpiEvent
from ...graph import Param
from ...registry import register


@register
class SpiDecodeNode:
    TYPE = "spi_decode"
    INPUTS = {"in": "digital"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "clk": Param("str", default="", doc="CLK 通道名（空 = 第 1 通道）"),
        "mosi": Param("str", default="", doc="MOSI 通道名（可选）"),
        "miso": Param("str", default="", doc="MISO 通道名（可选）"),
        "cs": Param("str", default="", doc="CS 通道名（可选；缺省按位计数分词）"),
        "cpol": Param("int", default=0, lo=0, hi=1, doc="时钟空闲电平 0/1"),
        "cpha": Param("int", default=0, lo=0, hi=1, doc="采样沿相位 0/1"),
        "word_bits": Param("int", default=8, lo=1, hi=32),
        "bit_order": Param("enum", default="msb", choices=("msb", "lsb")),
        "cs_active": Param("enum", default="low", choices=("low", "high")),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        wave: DigitalWave = inputs["in"]
        names = list(wave.channels)
        clk = params["clk"] or (names[0] if names else "")
        for role in ("clk", "mosi", "miso", "cs"):
            n = params.get(role) or ""
            if n and n not in names:
                raise ValueError(f"通道 {n!r} 不存在；可用: {names}")
        if not clk:
            raise ValueError("缺少 CLK 通道")
        mosi, miso, cs = params["mosi"] or None, params["miso"] or None, params["cs"] or None
        if not mosi and not miso:
            raise ValueError("MOSI 与 MISO 至少提供其一")

        rising = (params["cpol"] == 0) == (params["cpha"] == 0)
        clk_t, clk_lv = wave.edge_stream(clk)
        sample_target = 1 if rising else 0
        sample_times = clk_t[clk_lv == sample_target]

        wb = int(params["word_bits"])
        msb_first = params["bit_order"] == "msb"
        al = 0 if params["cs_active"] == "low" else 1

        events: list[SpiEvent] = []

        # CS 活动区间
        intervals: list[tuple[float, float]] | None = None
        if cs:
            intervals = []
            active = (wave.level_at(cs, wave.t_start) == al)
            t_on = wave.t_start if active else None
            cs_t, cs_lv = wave.edge_stream(cs)
            for t, lv in zip(cs_t, cs_lv):
                now_active = int(lv) == al
                if now_active and not active:
                    t_on = float(t)
                    active = True
                elif not now_active and active and t_on is not None:
                    intervals.append((t_on, float(t)))
                    active = False
            if active and t_on is not None:
                intervals.append((t_on, wave.t_end))
        else:
            events.append(SpiEvent("spi.warn", wave.t_start, wave.t_start,
                                   "无 CS 通道：按位计数分词", errors=["no-cs"], ann_class="warn"))

        def sample_pos(k: int) -> int:
            return (wb - 1 - k) if msb_first else k

        def flush_word(t0: float, t: float, w: int, mv: int, sv: int) -> None:
            label = _word_label(mv, sv, mosi is not None, miso is not None)
            events.append(SpiEvent("spi.word", t0, t, label, mosi=mv if mosi else None,
                                   miso=sv if miso else None, word_bits=wb))

        def flush_transfer(t0: float, t: float, words: list[tuple[int, int]]) -> None:
            label = f"{len(words)} 词"
            events.append(SpiEvent("spi.transfer", t0, t, label, words=list(words), word_bits=wb))

        if intervals is None:
            # 无 CS：全采集一个 transfer
            words, w, mv, sv, t0 = [], 0, 0, 0, None
            for t in sample_times:
                ft = float(t)
                if w == 0:
                    t0 = ft
                if mosi:
                    mv |= wave.level_at(mosi, ft) << sample_pos(w)
                if miso:
                    sv |= wave.level_at(miso, ft) << sample_pos(w)
                w += 1
                if w == wb:
                    flush_word(t0, ft, w, mv, sv)
                    words.append((mv if mosi else None, sv if miso else None))
                    w, mv, sv = 0, 0, 0
            flush_transfer(wave.t_start, wave.t_end, words)
            return {"out": events}

        # 有 CS：按区间解码
        si = 0
        for (t_on, t_off) in intervals:
            # 起点之后的采样沿（含区间内首沿）
            while si < len(sample_times) and float(sample_times[si]) < t_on:
                si += 1
            words, w, mv, sv, t0 = [], 0, 0, 0, None
            while si < len(sample_times) and float(sample_times[si]) <= t_off:
                ft = float(sample_times[si])
                if w == 0:
                    t0 = ft
                if mosi:
                    mv |= wave.level_at(mosi, ft) << sample_pos(w)
                if miso:
                    sv |= wave.level_at(miso, ft) << sample_pos(w)
                w += 1
                si += 1
                if w == wb:
                    flush_word(t0, ft, w, mv, sv)
                    words.append((mv if mosi else None, sv if miso else None))
                    w, mv, sv = 0, 0, 0
            if w != 0:
                events.append(SpiEvent("spi.warn", t0 or t_on, t_off, "CS 在词中翻转",
                                       errors=["cs-midword"], ann_class="warn"))
            flush_transfer(t_on, t_off, words)
        events.sort(key=lambda e: (e.t_start, e.t_end))
        return {"out": events}


def _word_label(mv: int, sv: int, has_mosi: bool, has_miso: bool) -> str:
    if has_mosi and has_miso:
        return f"M:0x{mv:02X} S:0x{sv:02X}"
    if has_mosi:
        return f"M:0x{mv:02X}"
    return f"S:0x{sv:02X}"
