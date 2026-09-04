"""Kingst VIS 裸二进制适配器：u16 LE 逐采样位域（bit i = 通道 i）。

无文件头、无采样率 —— options.sample_rate 必填（时间轴与 t_end 依赖它）。
通道名缺省 D0..D15（16 通道）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import Capture, CaptureMeta, DigitalWave
from .spec import AdapterSpec, OptionField


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    fs = opts.get("sample_rate")
    if not fs or float(fs) <= 0:
        raise IngestError(
            f"kingst_bin 文件不含采样率，必须在 options 里提供 sample_rate（Hz）。"
            f"Kingst 常见值: 200_000_000（可用 kingstvis MCP 的 get-sample-rate 查询）"
        )
    fs = float(fs)
    n_ch = int(opts.get("n_channels", 16))
    stream = np.fromfile(path, dtype="<u2")
    if stream.size == 0:
        raise IngestError(f"{path} 为空")
    initial = int(stream[0])
    idx = np.flatnonzero(np.diff(stream)) + 1
    wave = DigitalWave(
        channels=tuple(f"D{i}" for i in range(n_ch)),
        initial=initial,
        t_start=0.0,
        edges_t=idx / fs,
        edges_levels=stream[idx].astype(np.uint32),
        t_end=stream.size / fs,
        sample_rate=fs,
        n_samples=int(stream.size),
    )
    return Capture(
        meta=CaptureMeta(
            source_kind="kingst",
            format_key="kingst_bin",
            device=opts.get("device", "Kingst LA"),
            source_files=[str(path)],
            sample_rate=fs,
            extra={"n_samples": int(stream.size)},
        ),
        digital=wave,
    )


SPEC = AdapterSpec(
    key="kingst_bin",
    description="Kingst VIS 裸二进制（u16 LE 位域流；与 mcu_adc_bin 嗅探不可区分，须显式 format=）",
    load=load,
    options=(
        OptionField("sample_rate", "number",
                    "采样率 Hz，必填：文件无头。Kingst 常见 200_000_000"
                    "（kingstvis MCP get-sample-rate 可查）", required=True),
        OptionField("n_channels", "integer", "通道数（缺省 16，通道名 D0..Dn-1）"),
        OptionField("device", doc="设备显示名（缺省 Kingst LA）"),
    ),
)
