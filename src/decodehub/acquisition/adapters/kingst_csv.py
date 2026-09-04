"""Kingst VIS 数字 CSV 适配器。

怪癖（docs/40-acquisition.md）：分隔符是 ", "；通道名随软件语言（通道 N / Channel N）
→ 按列位置解析；文件不含采样率（options.sample_rate 可选补录，仅入元数据）。
"""

from __future__ import annotations

from pathlib import Path

from ...shared.waves import Capture, CaptureMeta
from .common import load_snapshot_csv
from .spec import AdapterSpec, OptionField


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


def _sniff(ctx) -> bool:
    for ln in ctx.lines[:3]:
        if ln.startswith("#"):
            continue
        cells = [c.strip() for c in ln.split(", ")]
        if len(cells) > 1 and cells[0] == "Time[s]":
            return True
    return False


SPEC = AdapterSpec(
    key="kingst_csv",
    description="Kingst VIS 数字 CSV（跳变表，表头 Time[s], 通道 0, …）",
    load=load,
    sniff=_sniff,
    sniff_hint="文本头（Time[s], …）",
    options=(
        OptionField("sample_rate", "number", "采样率补录（CSV 不含，仅入元数据）"),
        OptionField("device", doc="设备显示名（缺省 Kingst LA）"),
    ),
)
