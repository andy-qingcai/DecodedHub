"""切片节点单测：向量化滞回与朴素状态机等价 + 死区边界语义。"""

import numpy as np
import pytest

from decodehub.decode.nodes.slicer import slice_channel
from decodehub.shared.waves import AnalogChannel


def _ch(values, fs=1000.0):
    v = np.asarray(values, dtype=np.float32)
    return AnalogChannel(name="CH1", samples=v, t0=0.0, dt=1.0 / fs)


def _naive(v, thr, h):
    """评审前的朴素实现（逐采样状态机），作为等价性参考。"""
    hi, lo = thr + h / 2.0, thr - h / 2.0
    state = 1 if v[0] >= hi else (0 if v[0] <= lo else (1 if v[0] >= thr else 0))
    initial = state
    edges = []
    for i, x in enumerate(v):
        lvl = 1 if x >= hi else (0 if x <= lo else None)
        if lvl is not None and lvl != state:
            edges.append((float(i), lvl))
            state = lvl
    return initial, edges


def test_equivalence_with_naive_on_noisy_square():
    rng = np.random.default_rng(7)
    square = np.tile(np.concatenate([np.ones(50), np.zeros(50)]), 20)
    v = square + rng.normal(0, 0.05, square.size)  # 噪声不越过滞回带
    ch = _ch(v)
    thr, h, initial, edges = slice_channel(ch, None, None)
    ref_initial, ref_edges = _naive(v.astype(np.float64), thr, h)
    assert initial == ref_initial
    assert [(round(t, 9), lv) for t, lv in edges] == [(i / 1000.0, lv) for i, lv in ref_edges]


def test_constant_signal_no_edges():
    _thr, _h, initial, edges = slice_channel(_ch(np.full(100, 1.5)), None, None)
    assert edges == []
    assert initial == 1  # 恒定高


def test_all_deadband_initial_from_threshold():
    # 全部样本落在滞回带内（无定态）→ 无跳变；初值按与 thr 相对位置
    v = np.full(50, 1.0)
    thr, _h, initial, edges = slice_channel(_ch(v), 1.2, 0.5)
    assert edges == []
    assert initial == 0  # 1.0 < thr=1.2
    assert thr == 1.2


def test_first_sample_in_deadband_uses_threshold_rule():
    v = np.full(10, 1.0)
    v[5:] = 2.0  # 死区起步，随后越上阈
    _thr, _h, initial, edges = slice_channel(_ch(v), 1.0, 0.4)
    assert initial == 1  # 首样本 1.0 ≥ thr
    assert edges == []  # 状态本就是 1，越上阈不构成跳变


def test_negative_hysteresis_rejected():
    with pytest.raises(ValueError, match="滞回"):
        slice_channel(_ch(np.ones(10)), 1.0, -0.1)


def test_slicer_node_exposes_threshold_scalar():
    """scalar 输出端口：应用层据此回写 meta.threshold_v（docs/40 切片回写）。"""
    from decodehub.decode import Graph, evaluate, get_registry
    from decodehub.shared import Capture, CaptureMeta

    v = np.concatenate([np.zeros(50), np.ones(50)]) * 2.0
    cap = Capture(meta=CaptureMeta(source_kind="synth", format_key="synth"),
                  analog=[_ch(v)])
    g = Graph()
    g.add_node("apick", "analog_pick")
    g.add_node("slice", "slicer")
    g.add_edge("apick", "out", "slice", "in")
    memo = evaluate(g, get_registry(), ["slice"], sources={"apick": {"in": cap}})
    assert memo["slice"]["threshold"] == pytest.approx(1.0)  # (Vmin+Vmax)/2
