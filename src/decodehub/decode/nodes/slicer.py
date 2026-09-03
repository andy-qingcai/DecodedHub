"""阈值切片节点：模拟 → 数字（Schmitt 滞回）。

模拟→数字的唯一合法路径（ADR-002 规则 2）。阈值回写 meta.threshold_v 由
应用层在图外完成（图内保持纯函数，Capture 元数据只读）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ...shared.waves import AnalogChannel, DigitalWave
from ..graph import Param
from ..registry import register


def slice_channel(
    ch: AnalogChannel, threshold: float | None, hysteresis: float | None
) -> tuple[float, float, int, list[tuple[float, int]]]:
    """单通道切片。返回 (所用阈值, 所用滞回, 初始电平, [(t, 新电平)])。"""
    v = ch.samples.astype(np.float64)
    vmin, vmax = float(v.min()), float(v.max())
    thr = threshold if threshold is not None else (vmin + vmax) / 2.0
    h = hysteresis if hysteresis is not None else 0.2 * (vmax - vmin)
    hi, lo = thr + h / 2.0, thr - h / 2.0

    state = 1 if v[0] >= hi else (0 if v[0] <= lo else (1 if v[0] >= thr else 0))
    edges: list[tuple[float, int]] = []

    above = np.flatnonzero(v >= hi)
    below = np.flatnonzero(v <= lo)
    # 归并为时间序候选事件（下标序即时间序）
    cand = sorted(
        [(int(i), 1) for i in above] + [(int(i), 0) for i in below]
    )
    for i, lvl in cand:
        if lvl != state:
            edges.append((ch.time_at(i), lvl))
            state = lvl
    return thr, h, (1 if v[0] >= hi else (0 if v[0] <= lo else (1 if v[0] >= thr else 0))), edges


@register
class SlicerNode:
    TYPE = "slicer"
    INPUTS = {"in": "analog"}
    OUTPUTS = {"out": "digital"}
    PARAMS = {
        "threshold": Param("float", default=None, doc="阈值 V；缺省 = (Vmin+Vmax)/2"),
        "hysteresis": Param("float", default=None, doc="滞回宽度 V；缺省 = 0.2×幅值"),
        "names": Param("str_list", default=[], doc="输出数字通道名（缺省继承模拟通道名）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        channels: list[AnalogChannel] = inputs["in"]
        if not channels:
            raise ValueError("slicer 输入为空")
        thr_used: list[float] = []
        all_edges: list[tuple[float, int, int]] = []  # (t, bit, level)
        initial = 0
        names: list[str] = []
        for i, ch in enumerate(channels):
            thr, h, init, edges = slice_channel(ch, params["threshold"], params["hysteresis"])
            thr_used.append(thr)
            name = (params["names"][i] if params["names"] and i < len(params["names"]) else ch.name)
            names.append(name)
            initial |= (init & 1) << i
            for t, lvl in edges:
                all_edges.append((t, i, lvl))

        all_edges.sort(key=lambda e: e[0])
        segments: list[tuple[float, int]] = []
        snap = initial
        for t, bit, lvl in all_edges:
            new = (snap & ~(1 << bit)) | (lvl << bit)
            if new != snap:
                segments.append((t, new))
                snap = new
        t_start = min(ch.t0 for ch in channels)
        t_end = max(ch.t0 + ch.duration for ch in channels)
        wave = DigitalWave.from_segments(names, initial, segments, t_end, sample_rate=None)
        wave.t_start = t_start
        return {"out": wave}
