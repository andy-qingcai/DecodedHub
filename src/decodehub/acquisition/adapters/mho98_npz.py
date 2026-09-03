"""RIGOL MHO98 MCP 导出的 NPZ 适配器（键 t_s / v_V，可达 50M 点）。

均匀时只留 (t0, dt)；样本转 float32（内存策略见 docs/40-acquisition.md）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    p = Path(path)
    with np.load(p, allow_pickle=False) as z:
        if not {"t_s", "v_V"} <= set(z.files):
            raise IngestError(f"{p.name}: npz 键缺少 t_s/v_V（得到 {z.files}）")
        t = z["t_s"].astype(np.float64)
        v = z["v_V"].astype(np.float32)

    ch_name = opts.get("name") or p.stem.split("_")[0]
    times = None
    dt = None
    if t.size >= 2:
        d = np.diff(t)
        if np.ptp(d) <= 1e-12 * max(1.0, float(np.abs(d).max())):
            dt = float(np.median(d))
        else:
            times = t
    ch = AnalogChannel(name=ch_name, samples=v, t0=float(t[0]) if t.size else 0.0, dt=dt, times=times)
    meta = CaptureMeta(
        source_kind="mho98",
        format_key="mho98_npz",
        device="RIGOL MHO98",
        source_files=[str(p)],
        sample_rate=(1.0 / dt) if dt else None,
        extra={"points": int(t.size)},
    )
    return Capture(meta=meta, analog=[ch])
