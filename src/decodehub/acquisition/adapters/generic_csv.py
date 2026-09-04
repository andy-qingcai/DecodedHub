"""通用模拟 CSV 兜底适配器（x/t 列 + 一个或多个电压列；兼容 RIGOL 面板导出风格）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta
from .spec import AdapterSpec, OptionField, VOLT_COL


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    p = Path(path)
    header: list[str] | None = None
    rows: list[list[float]] = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith('"'):
                continue
            cells = [c.strip() for c in line.split(",")]
            try:
                nums = [float(c) for c in cells]
            except ValueError:
                if header is None:
                    header = cells
                    continue
                # RIGOL 面板 CSV 的 "Source,CH1" 等 key,value 行
                if len(cells) == 2 and header is not None:
                    continue
                raise IngestError(f"{p.name}: 无法解析行 {line[:60]!r}") from None
            rows.append(nums)

    if not rows or header is None:
        raise IngestError(f"{p.name}: 无法识别表头/数据")
    arr = np.array(rows, dtype=np.float64)
    low = [h.lower() for h in header]
    t_col = next((i for i, h in enumerate(low) if h in ("x", "t", "t_s", "time", "time_s")), 0)
    v_cols = [i for i in range(len(header)) if i != t_col]
    if not v_cols:
        raise IngestError(f"{p.name}: 未找到电压列")

    t_is_ms = "ms" in low[t_col]
    t = arr[:, t_col] * (1e-3 if t_is_ms else 1.0)

    channels: list[AnalogChannel] = []
    for i in v_cols:
        v = arr[:, i].astype(np.float32)
        times = None
        dt = None
        if t.size >= 2:
            d = np.diff(t)
            if np.ptp(d) <= 1e-9 * max(1.0, float(np.abs(d).max())):
                dt = float(np.median(d))
            else:
                times = t
        channels.append(
            AnalogChannel(name=header[i], samples=v, t0=float(t[0]), dt=dt, times=times)
        )
    meta = CaptureMeta(
        source_kind="generic",
        format_key="generic_csv",
        device=opts.get("device"),
        source_files=[str(p)],
        sample_rate=(1.0 / dt) if dt else None,
        extra={"rows": int(arr.shape[0])},
    )
    return Capture(meta=meta, analog=channels)


def _sniff(ctx) -> bool:
    hd = ctx.first_header()
    if hd is None:
        return False
    header, _data = hd
    low = [c.strip().lower() for c in header.split(",")]
    return (any(c in ("x", "t", "t_s", "time", "time_s") for c in low)
            and any(VOLT_COL.match(c) for c in low))


SPEC = AdapterSpec(
    key="generic_csv",
    description="通用模拟 CSV（x/t 列 + 电压列；兜底）",
    load=load,
    sniff=_sniff,
    sniff_hint="文本头（x/t 列 + 电压列）",
    options=(OptionField("device", doc="设备显示名"),),
)
