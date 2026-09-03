"""多源工程（Project）：多采集器捕获 + 每源时间偏移 → 合并为公共时间轴上的单一 Capture。

对齐方式（ADR-008；锚点对齐已被否决，不做任何跨设备共同信号检测）：
- 手动偏移：offsets={alias: 秒}（源时间 + offset = 工程时间）
- 墙钟时间戳：各源显式 t_wall → 差值即偏移（毫秒级粗对齐）

合并语义：
- 多源时数字/模拟通道名加 `alias:` 命名空间；单源保持原名（向后兼容）。
- 合并数字通道总数 ≤ 32（DigitalWave u32 位域限制，超出报明确错误）。
- merged() 结果按 (capture_ids, offsets) 记忆化；对齐变更自动失效。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..shared.waves import AnalogChannel, Capture, CaptureMeta, DigitalWave, TimeBase


@dataclass
class SourceEntry:
    """工程中的一个采集源：别名 + Capture + 时间偏移（源→工程）。"""

    alias: str
    capture: Capture
    offset: float = 0.0  # s；工程时间 = 源时间 + offset
    t_wall: datetime | None = None  # 采集起始墙钟（可选，粗对齐用）
    options: dict = field(default_factory=dict)  # 摄取时的选项（档案序列化用）

    @property
    def namespaced(self) -> bool:
        return ":" in self.alias  # 别名已含冒号则视为用户自行命名空间


@dataclass
class Project:
    """聚合根：N 个源对齐到公共时间轴；merged() 产出下游消费的单一 Capture。"""

    entries: list[SourceEntry] = field(default_factory=list)

    # ---- 管理 ------------------------------------------------------------

    def add(self, entry: SourceEntry) -> None:
        if any(e.alias == entry.alias for e in self.entries):
            raise ValueError(f"源别名重复: {entry.alias!r}")
        self.entries.append(entry)
        self._invalidate()

    def find(self, alias: str) -> SourceEntry:
        for e in self.entries:
            if e.alias == alias:
                return e
        raise KeyError(f"源别名不存在: {alias!r}；可用: {[e.alias for e in self.entries]}")

    @property
    def reference_alias(self) -> str | None:
        return self.entries[0].alias if self.entries else None

    def set_offsets(self, offsets: dict[str, float]) -> None:
        """增量设置偏移（未提及的源保持原值）。"""
        for alias, off in offsets.items():
            self.find(alias).offset = float(off)
        self._invalidate()

    def align_by_wallclock(self) -> dict[str, float]:
        """以参考源（首个）的 t_wall 为基准，差值即偏移。缺 t_wall 即报错。"""
        ref = self.entries[0]
        if ref.t_wall is None:
            raise ValueError(
                f"参考源 {ref.alias!r} 未提供 t_wall（add_source 时 options.t_wall 传入 ISO 时间）"
            )
        offsets = {}
        for e in self.entries:
            if e.t_wall is None:
                raise ValueError(f"源 {e.alias!r} 未提供 t_wall，无法墙钟对齐")
            offsets[e.alias] = (e.t_wall - ref.t_wall).total_seconds()
        self.set_offsets(offsets)
        return offsets

    # ---- 合并 ------------------------------------------------------------

    def _invalidate(self) -> None:
        self._merged: Capture | None = None
        self._merge_key: tuple | None = None

    def __post_init__(self) -> None:
        self._invalidate()

    @property
    def multi_source(self) -> bool:
        return len(self.entries) > 1

    def channel_name(self, entry: SourceEntry, name: str) -> str:
        if self.multi_source and not entry.namespaced:
            return f"{entry.alias}:{name}"
        return name

    def merged(self) -> Capture:
        """合并为公共时间轴上的单一 Capture（记忆化；偏移变更即失效）。"""
        key = (tuple((e.alias, e.capture.capture_id, e.offset) for e in self.entries),)
        if self._merged is not None and self._merge_key == key:
            return self._merged

        if not self.entries:
            raise ValueError("空工程（无采集源）")
        if len(self.entries) == 1:
            e = self.entries[0]
            cap = e.capture
            if e.offset:
                cap = _shift_capture(cap, e.offset)
                cap.capture_id = e.capture.capture_id
            else:
                cap = e.capture
            self._merged, self._merge_key = cap, key
            return cap

        dig_parts: list[tuple[float, DigitalWave, SourceEntry]] = []
        analogs: list[AnalogChannel] = []
        files: list[str] = []
        for e in self.entries:
            files += e.capture.meta.source_files
            if e.capture.digital is not None:
                dig_parts.append((e.offset, e.capture.digital, e))
            for ch in e.capture.analog:
                shifted = _shift_analog([ch], e.offset)[0]
                shifted.name = self.channel_name(e, ch.name)
                analogs.append(shifted)

        digital = None
        channels: list[str] = []
        if dig_parts:
            for _off, w, e in dig_parts:
                channels += [self.channel_name(e, c) for c in w.channels]
            if len(channels) > 32:
                raise ValueError(
                    f"合并后数字通道 {len(channels)} 超过 32（DigitalWave u32 位域限制）；"
                    f"请减少源通道或分多次会话分析"
                )
            digital = self._merge_digital(dig_parts)

        t_start = min(
            [_e.offset + w.t_start for _o, w, _e in dig_parts]
            + [e.offset + (c.t0 if c.times is None else float(c.times[0]))
               for e in self.entries for c in e.capture.analog]
        )
        t_end = max(
            [_e.offset + w.t_end for _o, w, _e in dig_parts]
            + [e.offset + a.t0 + a.duration for e in self.entries for a in e.capture.analog]
        )
        if digital is not None:
            digital.t_start = t_start
            digital.t_end = t_end

        pid = "proj-" + hashlib.sha256(
            "|".join(f"{e.capture.capture_id}:{e.offset}" for e in self.entries).encode()
        ).hexdigest()[:8]
        meta = CaptureMeta(
            source_kind="project",
            format_key="+".join(e.capture.meta.format_key for e in self.entries),
            device="multi-source",
            source_files=files,
            time_base=TimeBase.TRIGGER_RELATIVE,
            sample_rate=None,
            extra={
                "sources": [
                    {"alias": e.alias, "format": e.capture.meta.format_key,
                     "offset_s": e.offset, "t_wall": e.t_wall.isoformat() if e.t_wall else None}
                    for e in self.entries
                ],
            },
        )
        cap = Capture(meta=meta, digital=digital, analog=analogs, capture_id=pid)
        self._merged, self._merge_key = cap, key
        return cap

    def _merge_digital(self, parts: list[tuple[float, DigitalWave, SourceEntry]]) -> DigitalWave:
        """按源偏移平移后归并各数字组的跳变流，重排位域到连续低位。"""
        bit_of: dict[tuple[str, str], int] = {}
        names: list[str] = []
        bit = 0
        for _off, w, e in parts:
            for c in w.channels:
                full = self.channel_name(e, c)
                names.append(full)
                bit_of[(e.alias, c)] = bit
                bit += 1

        events: list[tuple[float, int, int]] = []  # (工程时间, 新bit, 电平)
        for off, w, e in parts:
            for c in w.channels:
                nb = bit_of[(e.alias, c)]
                t_arr, lv_arr = w.edge_stream(c)
                for t, lv in zip(t_arr, lv_arr):
                    events.append((float(t) + off, nb, int(lv)))

        events.sort(key=lambda x: x[0])
        # 合并后起点前各通道的初始电平（冻结外推语义）
        t_start = min(_e.offset + w.t_start for _o, w, _e in parts)
        initial = 0
        for off, w, e in parts:
            for c in w.channels:
                initial |= (w.level_at(c, t_start - off) & 1) << bit_of[(e.alias, c)]

        segs: list[tuple[float, int]] = []
        snap = initial
        i = 0
        n = len(events)
        # 容差归并：浮点 ulp 噪声可能把"同时翻转"（如 SCL 下降 + SDA 变化）拆成
        # 相差 ~1e-18s 的两条边——I2C 语义上应视为同一快照（容差远低于任何
        # 真实信号特征：200MHz LA 的采样粒度为 5ns）
        def _coincident(a: float, b: float) -> bool:
            return abs(a - b) <= max(1e-12, 1e-12 * abs(a))
        while i < n:
            t = events[i][0]
            xor = 0
            j = i
            while j < n and _coincident(events[j][0], t):
                xor ^= 1 << events[j][1]
                j += 1
            i = j
            snap ^= xor
            segs.append((t, snap))

        t_end = max(off + w.t_end for off, w, _e in parts)
        wave = DigitalWave.from_segments(names, initial, segs, t_end=t_end)
        return wave


def _shift_analog(channels: list[AnalogChannel], offset: float) -> list[AnalogChannel]:
    out = []
    for ch in channels:
        times = ch.times + offset if ch.times is not None else None
        out.append(AnalogChannel(
            name=ch.name, samples=ch.samples, units=ch.units, t0=ch.t0 + offset,
            dt=ch.dt, times=times, raw_scale=ch.raw_scale, raw_offset=ch.raw_offset,
        ))
    return out


def _shift_capture(cap: Capture, offset: float) -> Capture:
    """单源带偏移：模拟平移；数字重建跳变表。"""
    digital = None
    if cap.digital is not None:
        w = cap.digital
        digital = DigitalWave(
            channels=w.channels, initial=w.initial, t_start=w.t_start + offset,
            edges_t=w.edges_t + offset, edges_levels=w.edges_levels,
            t_end=w.t_end + offset, sample_rate=w.sample_rate, n_samples=w.n_samples,
        )
    return Capture(
        meta=CaptureMeta(**{**cap.meta.__dict__, "source_files": list(cap.meta.source_files)}),
        digital=digital, analog=_shift_analog(cap.analog, offset),
    )
