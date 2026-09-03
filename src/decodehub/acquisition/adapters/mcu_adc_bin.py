"""MCU ADC 裸二进制适配器：u16 LE 采样 dump（需 options.sample_rate）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    fs = opts.get("sample_rate")
    if not fs or float(fs) <= 0:
        raise IngestError(f"{path}: 裸二进制不含采样率，必须在 options 提供 sample_rate（Hz）")
    fs = float(fs)
    raw = np.fromfile(path, dtype="<u2")
    if raw.size == 0:
        raise IngestError(f"{path} 为空")
    vref = opts.get("vref")
    bits = opts.get("bits", 12)
    raw_scale = None
    if vref is not None:
        raw_scale = float(vref) / float(2 ** int(bits))
        samples = (raw * raw_scale).astype(np.float32)
    else:
        samples = raw.astype(np.float32)
    ch = AnalogChannel(
        name=opts.get("name", Path(path).stem),
        samples=samples,
        units="V" if raw_scale else "LSB",
        t0=0.0,
        dt=1.0 / fs,
        raw_scale=raw_scale,
    )
    meta = CaptureMeta(
        source_kind="mcu_adc",
        format_key="mcu_adc_bin",
        device=opts.get("device", "MCU ADC"),
        source_files=[str(path)],
        sample_rate=fs,
        extra={"n": int(raw.size)},
    )
    return Capture(meta=meta, analog=[ch])
