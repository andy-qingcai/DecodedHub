"""RIGOL MHO98 MCP 导出的 CSV 适配器。

布局：行1 `# MHO98 waveform source=CHANnel1 mode=NORMal points=1000`
     行2 `# xincrement=1e-05 xorigin=-0.005 xreference=0.0 yincrement=… yorigin=… yreference=…`
     行3 表头 `t_s,v_V`；随后数据行。
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta
from .spec import AdapterSpec, OptionField

_KV = re.compile(r"(\w+)=([-\d.eE+]+)")


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    p = Path(path)
    preamble: dict[str, str] = {}
    source = "CHAN1"
    with open(p, "r", encoding="utf-8-sig") as f:
        lines = [f.readline() for _ in range(3)]
    if not lines or not lines[0].startswith("# MHO98 waveform"):
        raise IngestError(f"{p.name}: 缺少 MHO98 前导行")
    source = lines[0].split("source=")[-1].split()[0] if "source=" in lines[0] else "CHAN1"
    preamble.update(_KV.findall(lines[1]) if len(lines) > 1 else {})

    arr = np.loadtxt(p, delimiter=",", skiprows=3, ndmin=2)
    if arr.shape[1] < 2:
        raise IngestError(f"{p.name}: 数据列不足（需要 t_s,v_V）")
    t = arr[:, 0].astype(np.float64)
    v = arr[:, 1].astype(np.float32)

    ch_name = opts.get("name") or source
    times = None
    dt = None
    if t.size >= 2:
        d = np.diff(t)
        if np.ptp(d) <= 1e-9 * max(1.0, float(np.abs(d).max())):
            # 以实测中值间距为准（前导 xincrement 仅作交叉校验，数据优先）
            dt = float(np.median(d))
            if preamble.get("xincrement") and abs(dt - float(preamble["xincrement"])) > 0.01 * dt:
                pass  # 前导与数据不符：相信数据（抽稀导出等场景）
        else:
            times = t
    ch = AnalogChannel(name=ch_name, samples=v, units="V", t0=float(t[0]), dt=dt, times=times)
    meta = CaptureMeta(
        source_kind="mho98",
        format_key="mho98_csv",
        device="RIGOL MHO98",
        source_files=[str(p)],
        sample_rate=(1.0 / dt) if dt else None,
        extra={"preamble": preamble, "points": int(arr.shape[0])},
    )
    return Capture(meta=meta, analog=[ch])


def _sniff(ctx) -> bool:
    return bool(ctx.lines) and ctx.lines[0].startswith("# MHO98 waveform")


SPEC = AdapterSpec(
    key="mho98_csv",
    description="RIGOL MHO98 MCP 导出 CSV（# 前导 + t_s,v_V）",
    load=load,
    sniff=_sniff,
    sniff_hint="文本头（# MHO98 前导）",
    options=(OptionField("name", doc="模拟通道名覆盖（缺省用前导 source=CHANx）"),),
)
