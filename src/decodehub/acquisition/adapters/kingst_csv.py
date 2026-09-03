"""Kingst VIS 数字 CSV 适配器。

怪癖（docs/40-acquisition.md）：分隔符是 ", "；通道名随软件语言（通道 N / Channel N）
→ 按列位置解析；文件不含采样率（options.sample_rate 可选补录，仅入元数据）。
"""

from __future__ import annotations

from pathlib import Path

from ...shared.waves import Capture, CaptureMeta
from .common import load_snapshot_csv


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    wave = load_snapshot_csv(
        Path(path),
        delimiter=", ",
        time_header="Time[s]",
        source_kind="kingst",
        format_key="kingst_csv",
        sample_rate=opts.get("sample_rate"),
        device=opts.get("device", "Kingst LA"),
    )
    return Capture(
        meta=CaptureMeta(
            source_kind="kingst",
            format_key="kingst_csv",
            device=opts.get("device", "Kingst LA"),
            source_files=[str(path)],
            sample_rate=opts.get("sample_rate"),
            extra={"note": "CSV 不含采样率；t_end 为最后一跳变时刻"},
        ),
        digital=wave,
    )
