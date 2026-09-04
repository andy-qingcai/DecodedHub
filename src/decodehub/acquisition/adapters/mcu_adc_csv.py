"""MCU ADC 串口记录 CSV 适配器。

变体：`time_ms,adc_raw` / `millis,value` / `voltage` 单列 / 无表头 1–2 数值列。
- 时间列 ms → s 归一；
- 单列（无时间）必须提供 options.sample_rate；
- 原始码值可经 options.vref + options.bits 换算为伏特（raw_scale 溯源保留）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta
from .spec import ADCISH, TIMEISH, AdapterSpec, OptionField, numeric_cells

_TIME_COL_MS = ("time_ms", "millis", "ms", "elapsed_ms")


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    p = Path(path)
    rows: list[list[float]] = []
    header: list[str] | None = None
    with open(p, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cells = [c.strip() for c in line.split(",")]
            try:
                rows.append([float(c) for c in cells])
            except ValueError:
                if header is None and rows == []:
                    header = [c.lower() for c in cells]
                    continue
                raise IngestError(f"{p.name}: 无法解析行 {line[:60]!r}") from None

    if not rows:
        raise IngestError(f"{p.name}: 无数据行")

    ncols = len(rows[0])
    if ncols not in (1, 2):
        raise IngestError(f"{p.name}: mcu_adc_csv 期望 1–2 列，得到 {ncols} 列")
    arr = np.array(rows, dtype=np.float64)

    time_ms = False
    if header:
        h0 = header[0]
        time_ms = any(h0 == k or h0.startswith(k) for k in _TIME_COL_MS)

    if ncols == 2:
        t = arr[:, 0] * (1e-3 if time_ms else 1.0)
        raw = arr[:, 1]
    else:
        fs = opts.get("sample_rate")
        if not fs or float(fs) <= 0:
            raise IngestError(
                f"{p.name}: 单列数据没有时间列，必须在 options 提供 sample_rate（Hz）"
            )
        fs = float(fs)
        t = np.arange(arr.shape[0], dtype=np.float64) / fs
        raw = arr[:, 0]

    vref = opts.get("vref")
    bits = opts.get("bits", 12)
    raw_scale = None
    if vref is not None:
        raw_scale = float(vref) / float(2 ** int(bits))
        samples = (raw * raw_scale).astype(np.float32)
    else:
        samples = raw.astype(np.float32)

    times = None
    dt = None
    if t.size >= 2:
        d = np.diff(t)
        if np.ptp(d) <= 1e-9 * max(1.0, float(np.abs(d).max())):
            dt = float(np.median(d))
        else:
            times = t
    ch = AnalogChannel(
        name=opts.get("name", "adc0"),
        samples=samples,
        units="V" if raw_scale else "LSB",
        t0=float(t[0]),
        dt=dt,
        times=times,
        raw_scale=raw_scale,
    )
    meta = CaptureMeta(
        source_kind="mcu_adc",
        format_key="mcu_adc_csv",
        device=opts.get("device", "MCU ADC"),
        source_files=[str(p)],
        sample_rate=(1.0 / dt) if dt else opts.get("sample_rate"),
        extra={"n": int(t.size), "header": header},
    )
    return Capture(meta=meta, analog=[ch])


def _sniff(ctx) -> bool:
    hd = ctx.first_header()
    if hd is None:
        return False
    header, data_lines = hd
    cells = [c.strip() for c in header.split(",")]
    if len(cells) >= 2 and TIMEISH.match(cells[0]):
        return True
    if len(cells) == 1 and ADCISH.match(cells[0]):
        return True
    # 无表头：前几行确为 1–2 列纯数值才认领
    if numeric_cells(header) and len(header.split(",")) <= 2:
        if all(numeric_cells(dl) is not None for dl in data_lines[:3]):
            return True
    return False


SPEC = AdapterSpec(
    key="mcu_adc_csv",
    description="MCU ADC 串口记录 CSV（time_ms,adc_raw 等变体）",
    load=load,
    sniff=_sniff,
    sniff_hint="文本头（时间/ADC 列或 1–2 数值列）",
    options=(
        OptionField("sample_rate", "number", "采样率 Hz（单列数据、无时间列时必填）"),
        OptionField("vref", "number", "ADC 参考电压 V（码值→伏特换算，保留 raw_scale 溯源）"),
        OptionField("bits", "integer", "ADC 位数（配 vref 用，缺省 12）"),
        OptionField("name", doc="模拟通道名（缺省 adc0）"),
        OptionField("device", doc="设备显示名（缺省 MCU ADC）"),
    ),
)
