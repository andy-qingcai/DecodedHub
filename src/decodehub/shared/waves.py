"""统一信号模型（C1 信号内核 / Shared Kernel）。

权威字段定义见 docs/40-acquisition.md。要点：
- DigitalWave: 多通道数字信号的位域跳变表 IR（ADR-003）。解码器只消费跳变流。
- AnalogChannel: (t0, dt, n) 紧凑时间轴 + float32 样本；非均匀时才物化 times。
- Capture: 归一化根对象。

电平外推语义（解码器的截断判定依赖此约定）：
- level_at(t < t_start) → initial；level_at(t > t_end) → 最后快照（冻结外推）。
- 采样点是否超出 t_end 由解码器自行判定（t_end = 已知信号视界）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import numpy as np


class TimeBase(Enum):
    TRIGGER_RELATIVE = "trigger_relative"  # 触发点 = 0（Kingst / MHO98 默认）
    ABSOLUTE = "absolute"  # 有 epoch 锚点


@dataclass
class CaptureMeta:
    source_kind: str  # kingst | mho98 | mcu_adc | saleae | generic
    format_key: str  # 嗅探格式键，见 docs/40-acquisition.md
    device: str | None = None
    source_files: list[str] = field(default_factory=list)
    captured_at: datetime | None = None
    time_base: TimeBase = TimeBase.TRIGGER_RELATIVE
    trigger_t: float | None = None
    sample_rate: float | None = None  # None = 文件未提供且未补参数
    probe_attenuation: float = 1.0
    threshold_v: float | None = None  # LA 阈值 / 切片所用阈值（回写）
    extra: dict = field(default_factory=dict)


@dataclass
class DigitalWave:
    """多通道数字 IR：位域跳变表。

    channels       有序通道名（保持源顺序），bit k = channels[k] 的电平。
    initial        t_start 时刻的位域快照。
    edges_t        严格递增的跳变时刻（秒）。
    edges_levels   每次跳变**之后**的位域快照（u32）。
    t_end          已知信号视界（秒，含）——截断判定基准。
    """

    channels: tuple[str, ...]
    initial: int
    t_start: float
    edges_t: np.ndarray  # f64[E]
    edges_levels: np.ndarray  # u32[E]
    t_end: float
    sample_rate: float | None = None  # 源采样率提示（元信息，解码不依赖）
    n_samples: int | None = None

    def __post_init__(self) -> None:
        self.edges_t = np.asarray(self.edges_t, dtype=np.float64)
        self.edges_levels = np.asarray(self.edges_levels, dtype=np.uint32)
        if len(self.channels) > 32:
            raise ValueError(f"DigitalWave 最多 32 通道（位域 u32），得到 {len(self.channels)}")
        if self.edges_t.size and self.edges_t.size != self.edges_levels.size:
            raise ValueError("edges_t 与 edges_levels 长度不一致")
        if self.edges_t.size >= 2 and not np.all(np.diff(self.edges_t) > 0):
            raise ValueError("edges_t 必须严格递增")
        if self.t_end < self.t_start:
            raise ValueError("t_end < t_start")

    # ---- 基本查询 -------------------------------------------------------

    @property
    def duration(self) -> float:
        return float(self.t_end - self.t_start)

    @property
    def n_edges(self) -> int:
        return int(self.edges_t.size)

    def bit_index(self, name: str) -> int:
        try:
            return self.channels.index(name)
        except ValueError:
            raise KeyError(
                f"数字通道 {name!r} 不存在；可用: {list(self.channels)}"
            ) from None

    def select(self, names: list[str] | tuple[str, ...]) -> "DigitalWave":
        """抽取通道子集并把位重编号到连续低位；丢弃不引起电平变化的跳变。"""
        idx = [self.bit_index(n) for n in names]
        lv = self.edges_levels.astype(np.uint32)
        new_lv = np.zeros_like(lv)
        for new_bit, old_bit in enumerate(idx):
            new_lv |= ((lv >> np.uint32(old_bit)) & np.uint32(1)) << np.uint32(new_bit)
        keep_initial = 0
        for new_bit, old_bit in enumerate(idx):
            keep_initial |= ((self.initial >> old_bit) & 1) << new_bit
        prev = np.empty_like(new_lv)
        if new_lv.size:
            prev[0] = keep_initial
            prev[1:] = new_lv[:-1]
        changed = new_lv != prev
        return DigitalWave(
            channels=tuple(names),
            initial=int(keep_initial),
            t_start=self.t_start,
            edges_t=self.edges_t[changed],
            edges_levels=new_lv[changed],
            t_end=self.t_end,
            sample_rate=self.sample_rate,
            n_samples=self.n_samples,
        )

    def edge_stream(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """单通道跳变流：(时刻数组, 跳变后电平数组[0/1])。"""
        b = self.bit_index(name)
        bit = np.uint32(1) << np.uint32(b)
        lv = self.edges_levels & bit
        changed = lv != np.concatenate(([self.initial & bit], lv[:-1])) if lv.size else np.zeros(0, dtype=bool)
        return self.edges_t[changed], (lv[changed] >> np.uint32(b)).astype(np.uint8)

    def level_at(self, name: str, t: float) -> int:
        """时刻 t 的电平（冻结外推，见模块 docstring）。O(log E)。"""
        b = self.bit_index(name)
        i = int(np.searchsorted(self.edges_t, t, side="right"))
        snap = self.initial if i == 0 else int(self.edges_levels[i - 1])
        return (snap >> b) & 1

    def levels_during(self, name: str, t: float, dt: float) -> bool:
        """区间 [t, t+dt) 内电平是否恒定（用于 BREAK/长电平判定）。"""
        b = self.bit_index(name)
        i0 = int(np.searchsorted(self.edges_t, t, side="right"))
        i1 = int(np.searchsorted(self.edges_t, t + dt, side="left"))
        seg = self.edges_levels[i0:i1]
        bit = np.uint32(1) << np.uint32(b)
        hits = np.count_nonzero(seg & bit)
        return hits == 0 or hits == (i1 - i0)

    def to_bool_array(self, name: str, n: int | None = None) -> np.ndarray:
        """重物化逐采样布尔数组（测试/绘图用；需 sample_rate 或 n）。"""
        if self.sample_rate is None and n is None:
            raise ValueError("to_bool_array 需要 sample_rate 或显式 n")
        if n is None:
            n = self.n_samples or int((self.t_end - self.t_start) * self.sample_rate) + 1
        arr = np.zeros(n, dtype=bool)
        b = self.bit_index(name)
        cur = (self.initial >> b) & 1
        idx = 0
        if cur:
            arr[:] = True
        for t, snap in zip(self.edges_t, self.edges_levels):
            i = int(round((t - self.t_start) * (self.sample_rate or 0)))
            if i >= n:
                break
            cur = (int(snap) >> b) & 1
            arr[i:] = cur
        return arr

    @staticmethod
    def from_bool_array(
        values: np.ndarray, name: str, fs: float, t0: float = 0.0, t_end: float | None = None
    ) -> "DigitalWave":
        """逐采样布尔数组 → 压缩跳变表（synth/测试构造入口）。"""
        v = np.asarray(values).astype(np.uint8)
        idx = np.flatnonzero(np.diff(v)) + 1
        return DigitalWave(
            channels=(name,),
            initial=int(v[0]) if v.size else 0,
            t_start=t0,
            edges_t=t0 + idx / fs,
            edges_levels=v[idx].astype(np.uint32),
            t_end=t_end if t_end is not None else t0 + v.size / fs,
            sample_rate=fs,
            n_samples=int(v.size),
        )

    @staticmethod
    def from_segments(
        channels: list[str],
        initial: int,
        segments: list[tuple[float, int]],
        t_end: float,
        t_start: float = 0.0,
        sample_rate: float | None = None,
        n_samples: int | None = None,
    ) -> "DigitalWave":
        """由 [(t, 跳变后位域快照)] 构造（适配器/合成器入口）。

        t_start = 采集起点（首段之前电平 = initial；MHO98 等源可为负）。
        自动丢弃与前一快照相同的段（合成器可能产生无变化沿）。
        """
        # 同一时刻多个快照 → 取最后；再按时间序去重无变化段
        merged: dict[float, int] = {}
        for t, snap in segments:
            merged[t] = snap
        times: list[float] = []
        levels: list[int] = []
        prev = initial
        for t in sorted(merged):
            snap = merged[t]
            if snap != prev:
                times.append(t)
                levels.append(snap)
                prev = snap
        return DigitalWave(
            channels=tuple(channels),
            initial=initial,
            t_start=t_start,
            edges_t=np.array(times, dtype=np.float64),
            edges_levels=np.array(levels, dtype=np.uint32),
            t_end=t_end,
            sample_rate=sample_rate,
            n_samples=n_samples,
        )


@dataclass
class AnalogChannel:
    """单通道模拟信号：(t0, dt, n) 紧凑时间轴 + float 样本。"""

    name: str
    samples: np.ndarray  # 物理单位（V）
    units: str = "V"
    t0: float = 0.0
    dt: float | None = None  # 均匀步长；None 则 times 必填
    times: np.ndarray | None = None  # 仅非均匀时物化
    raw_scale: float | None = None  # 码值→电压 系数（mcu_adc 溯源）
    raw_offset: float = 0.0

    def __post_init__(self) -> None:
        self.samples = np.asarray(self.samples)
        if self.dt is None and self.times is None:
            raise ValueError(f"模拟通道 {self.name}: dt 与 times 至少其一")
        if self.times is not None and self.times.size != self.samples.size:
            raise ValueError(f"模拟通道 {self.name}: times 与 samples 长度不一致")

    @property
    def n(self) -> int:
        return int(self.samples.size)

    @property
    def duration(self) -> float:
        return float(self.times[-1] - self.times[0]) if self.times is not None else float((self.n - 1) * (self.dt or 0.0))

    def times_array(self) -> np.ndarray:
        """按需物化时间数组（均匀时 O(n) 现算）。"""
        if self.times is not None:
            return self.times
        return self.t0 + np.arange(self.n, dtype=np.float64) * float(self.dt or 0.0)

    def time_at(self, i: int) -> float:
        if self.times is not None:
            return float(self.times[i])
        return self.t0 + i * float(self.dt or 0.0)


@dataclass
class Capture:
    """归一化采集根对象。"""

    meta: CaptureMeta
    digital: DigitalWave | None = None
    analog: list[AnalogChannel] = field(default_factory=list)
    capture_id: str = ""

    @property
    def duration(self) -> float:
        ends = [self.digital.t_end] if self.digital else []
        ends += [ch.t0 + ch.duration for ch in self.analog]
        return max(ends) if ends else 0.0

    def channel_names(self) -> dict:
        return {
            "digital": list(self.digital.channels) if self.digital else [],
            "analog": [ch.name for ch in self.analog],
        }


def make_capture_id(path: str | Path) -> str:
    """文件名 slug + 内容指纹（前 64KB + 大小），制品目录键。"""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(65536))
    h.update(str(p.stat().st_size).encode())
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in p.stem)[:40]
    return f"{slug}-{h.hexdigest()[:8]}"
