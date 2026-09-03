"""协议节点公共辅助（ADR-012）。"""

from __future__ import annotations

import numpy as np

from ...shared.waves import AnalogChannel


def pick_channel(channels: list[AnalogChannel], name: str) -> AnalogChannel:
    if not channels:
        raise ValueError("输入无模拟通道")
    if not name:
        return channels[0]
    for ch in channels:
        if ch.name == name:
            return ch
    raise ValueError(f"模拟通道 {name!r} 不存在；可用: {[c.name for c in channels]}")


def require_uniform(ch: AnalogChannel) -> tuple[np.ndarray, float]:
    if ch.dt is None:
        raise ValueError(
            f"通道 {ch.name!r} 时间轴非均匀（均匀采样是相关/解调的前提）；"
            f"请用均匀采样导出重试"
        )
    return np.asarray(ch.samples, dtype=np.float64), float(ch.dt)
