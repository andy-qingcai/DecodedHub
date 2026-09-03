"""Saleae Logic 2 数字 CSV 适配器（表头 `Time [s],Channel 0,…`，逗号分隔）。"""

from __future__ import annotations

from pathlib import Path

from ...shared.waves import Capture, CaptureMeta
from .common import load_snapshot_csv


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    wave = load_snapshot_csv(
        Path(path),
        delimiter=",",
        time_header="Time [s]",
        source_kind="saleae",
        format_key="saleae_csv",
        sample_rate=opts.get("sample_rate"),
        device=opts.get("device", "Saleae Logic"),
    )
    return Capture(
        meta=CaptureMeta(
            source_kind="saleae",
            format_key="saleae_csv",
            device=opts.get("device", "Saleae Logic"),
            source_files=[str(path)],
            sample_rate=opts.get("sample_rate"),
            extra={"note": "CSV 不含采样率；t_end 为最后一跳变时刻"},
        ),
        digital=wave,
    )
