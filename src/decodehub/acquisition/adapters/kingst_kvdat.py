"""Kingst VIS 工程文件（.kvdat）适配器 —— 自描述的二进制跳变档案。

布局（逆向自真实样本，见 docs/40-acquisition.md）：
  [XML <settings> 前导（变长）] "\\n" b"kvdat\\0\\0\\0"
  u64 LE × 4: n_samples, sample_rate, trigger_pos, n_channels
  每通道 0..n_channels-1 各一块:
      u32 常量 0x00442323 | u8 ch_index | u8 initial_level | u16 保留 | u64 record_count
      record_count × 5 字节记录: u32 位置索引 + u8 flag(≡0)
  末条记录 = (n_samples, 0) 为终结符（丢弃）。

记录按通道分块 → 通道归属直接可知；各通道跳变合并为全局时间序位域快照。
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import Capture, CaptureMeta, DigitalWave

_MAGIC = b"kvdat\x00\x00\x00"
_CH_STRUCT = struct.Struct("<IBBHQ")  # magic, ch_index, initial, res, record_count
_REC_STRUCT = struct.Struct("<IB")


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    data = Path(path).read_bytes()
    off = data.find(_MAGIC)
    if off < 0:
        raise IngestError(f"{path}: 未找到 kvdat 魔数")
    off += len(_MAGIC)
    try:
        n_samples, sample_rate, trigger_pos, n_channels = struct.unpack_from("<QQQQ", data, off)
        off += 32

        toggles: list[tuple[float, int]] = []  # (t, bit_index)
        initial_mask = 0
        names: list[str] = []
        for _ in range(n_channels):
            magic, ch_index, initial_level, _res, record_count = _CH_STRUCT.unpack_from(data, off)
            off += _CH_STRUCT.size
            if magic != 0x00442323:
                raise IngestError(f"{path}: 通道描述头魔数不符 (0x{magic:08X})")
            initial_mask |= (initial_level & 1) << ch_index
            names.append(f"D{ch_index}")
            for _ in range(record_count):
                pos, _flag = _REC_STRUCT.unpack_from(data, off)
                off += _REC_STRUCT.size
                if pos >= n_samples:  # 终结符或越界
                    continue
                toggles.append((pos / sample_rate, ch_index))

        toggles.sort()
        edges_t: list[float] = []
        edges_lv: list[int] = []
        snap = initial_mask
        i = 0
        while i < len(toggles):
            t, bit = toggles[i]
            xor = 0
            # 同一位置可能多通道同时翻转 → 合并为一次位域跳变
            while i < len(toggles) and toggles[i][0] == t:
                xor ^= 1 << toggles[i][1]
                i += 1
            snap ^= xor
            edges_t.append(t)
            edges_lv.append(snap)

        wave = DigitalWave(
            channels=tuple(names),
            initial=initial_mask,
            t_start=0.0,
            edges_t=np.array(edges_t, dtype=np.float64),
            edges_levels=np.array(edges_lv, dtype=np.uint32),
            t_end=n_samples / sample_rate,
            sample_rate=float(sample_rate),
            n_samples=int(n_samples),
        )
        meta = CaptureMeta(
            source_kind="kingst",
            format_key="kingst_kvdat",
            device=opts.get("device", "Kingst LA"),
            source_files=[str(path)],
            sample_rate=float(sample_rate),
            trigger_t=float(trigger_pos) / sample_rate if trigger_pos else 0.0,
            extra={"n_samples": int(n_samples), "n_channels": int(n_channels)},
        )
        return Capture(meta=meta, digital=wave)
    except struct.error as e:
        raise IngestError(f"{path}: kvdat 结构解析越界: {e}") from e
