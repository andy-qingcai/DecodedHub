"""matplotlib 绘图（Agg、图文配对、CJK 安全；规范见 docs/42-render.md）。

- timing_plot: 多通道数字方波 + 事件 span 着色 + 编号标注（编号 ↔ Markdown 表）
- analog_plot: 模拟波形（min/max 包络抽取）+ 阈值线 + 数字轨迹叠加
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..decode.events import DecodedEvent
from ..decode.presentation import presentation_of
from ..shared.waves import AnalogChannel, DigitalWave

_ANN_COLORS = {
    "start": "tab:blue", "stop": "tab:red", "data": "tab:green",
    "ack": "tab:purple", "warn": "tab:orange", "err": "tab:red",
}
_LINESTYLES = ("-", "--", "-.", ":")


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Heiti TC",
                                       "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _window(arr: np.ndarray, t_min: float, t_max: float) -> np.ndarray:
    return arr[(arr >= t_min) & (arr <= t_max)]


def timing_plot(
    digital: DigitalWave,
    events: list[DecodedEvent],
    path: str | Path,
    t_min: float | None = None,
    t_max: float | None = None,
    max_frames: int = 60,
    dpi: int = 150,
    title: str = "",
) -> Path:
    plt = _setup_matplotlib()
    from matplotlib.ticker import EngFormatter

    t_lo = t_min if t_min is not None else digital.t_start
    t_hi = t_max if t_max is not None else digital.t_end
    if t_hi <= t_lo:
        t_hi = t_lo + 1e-9

    chs = list(digital.channels)
    n = len(chs)
    fig, ax = plt.subplots(figsize=(10, 1.8 + 0.8 * n), dpi=dpi)

    span_extra = (t_hi - t_lo) * 0.005
    for i, name in enumerate(chs):
        y0 = 2 * (n - 1 - i)
        b = digital.bit_index(name)
        et = _window(digital.edges_t, t_lo - span_extra, t_hi + span_extra)
        mask = np.zeros(et.size, dtype=bool)
        prev = digital.initial
        sel = []
        # 重建该通道在窗口内的电平序列（含窗口前最后一个快照）
        idx_before = int(np.searchsorted(digital.edges_t, t_lo, side="right"))
        prev_snap = int(digital.edges_levels[idx_before - 1]) if idx_before > 0 else digital.initial
        cur = (prev_snap >> b) & 1
        t_pts: list[float] = [t_lo]
        lv_pts: list[int] = [cur]
        for t, snap in zip(et, digital.edges_levels[(digital.edges_t >= t_lo - span_extra)
                                                    & (digital.edges_t <= t_hi + span_extra)]):
            new = (int(snap) >> b) & 1
            if new != cur:
                t_pts.append(float(t))
                lv_pts.append(new)
                cur = new
        t_pts.append(t_hi)
        lv_pts.append(cur)
        tt = np.array(t_pts)
        yy = y0 + np.array(lv_pts, dtype=float)
        ax.step(tt, yy, where="post", lw=1.4,
                linestyle=_LINESTYLES[i % len(_LINESTYLES)], color=f"C{i % 10}")
        ax.text(t_lo - (t_hi - t_lo) * 0.012, y0 + 0.45, name, ha="right", va="center", fontsize=9)

    # 事件 span（窗口内，cap 到 max_frames，图内只标编号；画哪些协议族查呈现注册表）
    vis = [e for e in events if e.t_end >= t_lo and e.t_start <= t_hi
           and (p := presentation_of(e.kind)) is not None and p.plot_family
           and not e.kind.endswith(".warn")]
    shown = vis[:max_frames]
    y_top = 2 * n - 0.25
    for k, ev in enumerate(shown, 1):
        color = _ANN_COLORS.get(ev.ann_class, "tab:gray")
        a0, a1 = max(ev.t_start, t_lo), min(ev.t_end, t_hi)
        ax.axvspan(a0, a1, color=color, alpha=0.15)
        w_px = (a1 - a0) / (t_hi - t_lo) * 1500 * dpi / 150
        if w_px >= 40:
            ax.annotate(str(k), xy=((a0 + a1) / 2, y_top), ha="center", fontsize=8,
                        color=color, fontweight="bold")
    if len(vis) > max_frames:
        ax.text(t_hi, y_top, f" …还有 {len(vis) - max_frames} 帧", ha="right",
                fontsize=8, color="tab:gray")

    ax.set_ylim(-0.5, 2 * n)
    ax.set_yticks([])
    ax.set_xlim(t_lo, t_hi)
    ax.xaxis.set_major_formatter(EngFormatter(unit="s"))
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.set_title(title or f"数字时序（{n} 通道 · {len(shown)} 事件 span）", fontsize=10)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p)
    plt.close(fig)
    # 超过 ~600KB 自动降 dpi 重绘一次
    if p.stat().st_size > 600_000 and dpi > 100:
        return timing_plot(digital, events, path, t_min, t_max, max_frames, dpi=110, title=title)
    return p


def _decimate_minmax(t: np.ndarray, v: np.ndarray, target: int = 3000):
    if v.size <= target:
        return t, v
    bucket = int(np.ceil(v.size / (target / 2)))
    n2 = v.size // bucket
    t2, v2 = t[: n2 * bucket].reshape(n2, bucket), v[: n2 * bucket].reshape(n2, bucket)
    tmin, tmax_ = t2.min(axis=1), t2.max(axis=1)
    vmin, vmax_ = v2.min(axis=1), v2.max(axis=1)
    order = np.argsort(np.stack([tmin, tmax_]), axis=0)
    tt = np.stack([tmin, tmax_])
    vv = np.stack([vmin, vmax_])
    return tt[order, np.arange(n2)].ravel(), vv[order, np.arange(n2)].ravel()


def analog_plot(
    channels: list[AnalogChannel],
    path: str | Path,
    digital: DigitalWave | None = None,
    threshold: float | None = None,
    t_min: float | None = None,
    t_max: float | None = None,
    dpi: int = 150,
    title: str = "",
    events: list[DecodedEvent] | None = None,
) -> Path:
    """模拟波形图；events 提供时在首个轴上叠加事件 span（如上行突发帧，ADR-010）。"""
    plt = _setup_matplotlib()
    from matplotlib.ticker import EngFormatter

    n = len(channels) + (1 if digital else 0)
    fig, axes = plt.subplots(n, 1, figsize=(10, 1.6 * n + 0.8), dpi=dpi, sharex=True,
                             squeeze=False)
    for i, ch in enumerate(channels):
        ax = axes[i][0]
        t = ch.times_array()
        v = ch.samples.astype(np.float64)
        if t_min is not None or t_max is not None:
            lo = t_min if t_min is not None else t[0]
            hi = t_max if t_max is not None else t[-1]
            m = (t >= lo) & (t <= hi)
            t, v = t[m], v[m]
        td, vd = _decimate_minmax(t, v)
        ax.plot(td, vd, lw=0.8, color=f"C{i % 10}")
        for ev in events or []:
            if ev.kind.endswith(".warn"):
                continue
            a0 = max(ev.t_start, t[0]); a1 = min(ev.t_end, t[-1])
            if a1 <= a0:
                continue
            ax.axvspan(a0, a1, color=_ANN_COLORS.get(ev.ann_class, "tab:gray"),
                       alpha=0.18)
        if threshold is not None:
            lo_v, hi_v = float(vd.min()), float(vd.max())
            pad = 0.1 * (hi_v - lo_v) if hi_v > lo_v else 0.5
            ax.axhline(threshold, color="tab:red", ls="--", lw=1,
                       label=f"阈值 {threshold:.3g} V")
            ax.set_ylim(lo_v - pad, hi_v + pad)
            ax.legend(fontsize=8, loc="upper right")
        ax.set_ylabel(f"{ch.name}\n({ch.units})", fontsize=8)
        ax.grid(alpha=0.3)

    if digital is not None:
        ax = axes[-1][0]
        t_lo = t_min if t_min is not None else digital.t_start
        t_hi = t_max if t_max is not None else digital.t_end
        for i, name in enumerate(digital.channels):
            y0 = 2 * (len(digital.channels) - 1 - i)
            cur = digital.level_at(name, t_lo)
            pts_t, pts_l = [t_lo], [cur]
            et = digital.edges_t
            lv = digital.edges_levels
            m = (et >= t_lo) & (et <= t_hi)
            b = digital.bit_index(name)
            for t, snap in zip(et[m], lv[m]):
                new = (int(snap) >> b) & 1
                if new != cur:
                    pts_t.append(float(t))
                    pts_l.append(new)
                    cur = new
            pts_t.append(t_hi)
            pts_l.append(cur)
            ax.step(np.array(pts_t), y0 + np.array(pts_l, dtype=float), where="post",
                    lw=1.2, color=f"C{(i + 1) % 10}",
                    linestyle=_LINESTYLES[i % len(_LINESTYLES)])
        ax.set_ylim(-0.5, 2 * len(digital.channels))
        ax.set_yticks([])
        ax.set_ylabel("切片输出", fontsize=8)

    axes[-1][0].xaxis.set_major_formatter(EngFormatter(unit="s"))
    if t_min is not None or t_max is not None:
        axes[-1][0].set_xlim(
            t_min if t_min is not None else axes[-1][0].get_xlim()[0],
            t_max if t_max is not None else axes[-1][0].get_xlim()[1],
        )
    fig.suptitle(title or "模拟波形" + ("（含阈值切片）" if digital else ""), fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p)
    plt.close(fig)
    if p.stat().st_size > 600_000 and dpi > 100:
        return analog_plot(channels, path, digital, threshold, t_min, t_max,
                           dpi=110, title=title, events=events)
    return p
