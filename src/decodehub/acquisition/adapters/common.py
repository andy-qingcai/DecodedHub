"""数字快照 CSV 的公共解析（kingst_csv 与 saleae_csv 同构，仅分隔符/表头不同）。

行语义：任一通道变化才出一行，行内是全通道电平快照（跳变后）。
首行（t=0）提供 initial 位域。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import DigitalWave


def load_snapshot_csv(
    path: Path,
    *,
    delimiter: str,
    time_header: str,
    source_kind: str,
    format_key: str,
    sample_rate: float | None,
    device: str | None,
) -> DigitalWave:
    rows: list[tuple[float, list[int]]] = []
    channels: list[str] | None = None
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            cells = [c.strip() for c in line.split(delimiter)]
            if channels is None:
                if not cells or cells[0] != time_header:
                    raise IngestError(
                        f"{path.name}:{lineno} 表头不符合预期（首列应为 {time_header!r}），得到 {line[:80]!r}"
                    )
                channels = cells[1:]
                continue
            try:
                t = float(cells[0])
                vals = [int(c) for c in cells[1:]]
            except ValueError as e:
                raise IngestError(f"{path.name}:{lineno} 数据行解析失败: {line[:80]!r}") from e
            if len(vals) != len(channels):
                raise IngestError(
                    f"{path.name}:{lineno} 列数 {len(vals)} 与表头 {len(channels)} 不一致"
                )
            rows.append((t, vals))

    if channels is None or not rows:
        raise IngestError(f"{path.name} 缺少表头或数据行")

    initial = 0
    for i, v in enumerate(rows[0][1]):
        initial |= (v & 1) << i

    edges_t: list[float] = []
    edges_lv: list[int] = []
    prev = initial
    for t, vals in rows[1:]:
        snap = 0
        for i, v in enumerate(vals):
            snap |= (v & 1) << i
        if snap != prev and t > (edges_t[-1] if edges_t else float("-inf")):
            edges_t.append(t)
            edges_lv.append(snap)
            prev = snap

    t_end = rows[-1][0] if len(rows) > 1 else rows[0][0]
    return DigitalWave(
        channels=tuple(channels),
        initial=initial,
        t_start=rows[0][0],
        edges_t=np.array(edges_t, dtype=np.float64),
        edges_levels=np.array(edges_lv, dtype=np.uint32),
        t_end=t_end,
        sample_rate=sample_rate,
        n_samples=None,
    )
