"""MCU ADC 裸二进制适配器：u16 LE 采样 dump（需 options.sample_rate）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import AnalogChannel, Capture, CaptureMeta
from .spec import AdapterSpec, OptionField


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


def _sniff(ctx) -> bool:
    return not ctx.textual and ctx.size % 2 == 0 and ctx.size >= 2


SPEC = AdapterSpec(
    key="mcu_adc_bin",
    description="MCU ADC 裸二进制（u16 LE 采样 dump；偶数大小兜底嗅探）",
    load=load,
    sniff=_sniff,
    sniff_hint="偶数大小裸二进制",
    options=(
        OptionField("sample_rate", "number", "采样率 Hz，必填：裸二进制不含时间信息", required=True),
        OptionField("vref", "number", "ADC 参考电压 V（码值→伏特换算，保留 raw_scale 溯源）"),
        OptionField("bits", "integer", "ADC 位数（配 vref 用，缺省 12）"),
        OptionField("name", doc="模拟通道名（缺省文件名）"),
        OptionField("device", doc="设备显示名（缺省 MCU ADC）"),
    ),
)
