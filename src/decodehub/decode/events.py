"""解码事件模型（发布语言，呈现层只读消费）。

解码错误是事件字段而非异常（ADR-004）。所有事件全局时间有序（Saleae 不变量）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class DecodedEvent:
    kind: str
    t_start: float
    t_end: float
    label: str
    errors: list[str] = field(default_factory=list)
    ann_class: str = "data"  # start/stop/data/ack/warn/err

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UartEvent(DecodedEvent):
    value: int = 0
    parity: str = "N"
    data_bits: int = 8


@dataclass
class I2cEvent(DecodedEvent):
    address: int | None = None
    is_10bit: bool = False
    read: bool | None = None
    data_bytes: list[int] = field(default_factory=list)
    acks: list[bool] = field(default_factory=list)  # True = ACK
    byte_index: int = 0


@dataclass
class SpiEvent(DecodedEvent):
    mosi: int | None = None
    miso: int | None = None
    word_bits: int = 8
    words: list[tuple[int, int]] = field(default_factory=list)  # transfer 级


@dataclass
class UplinkEvent(DecodedEvent):
    """上行 DSSS 帧（kind ∈ uplink.frame / uplink.warn）。

    t_start = 帧首符号（前导第一符号）的解扩相关峰时刻；value = 数据 bit 组装值。
    """

    value: int = 0
    data_bits: list[int] = field(default_factory=list)
    pream_ok: bool = True
    confidence: float = 0.0
    burst: int = 0


@dataclass
class DownlinkEvent(DecodedEvent):
    """下行 DBPSK 包（kind ∈ downlink.packet / downlink.warn）。

    槽位锚定在上行帧网格（delta 自校准，ADR-011）；value = 16 差分数据位
    （1 = 相对前符号相位翻转）组装值；value_inv 为反相解读（排查用）。
    """

    value: int = 0
    value_inv: int = 0
    bits: list[int] = field(default_factory=list)
    slot: int = 0
    frame: int = 0
    fc_est: float = 0.0
    confidence: float = 0.0


@dataclass
class DecodeReport:
    protocol: str
    params: dict
    events: list[DecodedEvent]
    node_id: str = ""
    wall_ms: float = 0.0

    def counts(self) -> dict:
        by_kind: dict[str, int] = {}
        n_err = 0
        for ev in self.events:
            by_kind[ev.kind] = by_kind.get(ev.kind, 0) + 1
            n_err += len(ev.errors)
        return {"total": len(self.events), "by_kind": by_kind, "errors": n_err}

    def to_json(self) -> dict:
        return {
            "protocol": self.protocol,
            "params": self.params,
            "counts": self.counts(),
            "node_id": self.node_id,
            "wall_ms": round(self.wall_ms, 3),
            "events": [e.to_dict() for e in self.events],
        }


def timed_report(protocol: str, params: dict, events: list[DecodedEvent], node_id: str) -> DecodeReport:
    events.sort(key=lambda e: (e.t_start, e.t_end))
    return DecodeReport(
        protocol=protocol, params=params, events=events, node_id=node_id, wall_ms=0.0
    )
