"""I2C 解码器（docs/41-decode.md §3.2）。

- START/STOP = SCL 高电平期间的 SDA 变化；重复 START = 开放传输中的 START；
- 数据位在 SCL 上升沿采样（位采样只发生在实际上升沿 ⇒ 时钟拉伸免疫，ADR-005）；
- ACK = 第 9 位（0=ACK）；7-bit 与 10-bit（首字节 11110xx）地址；
- 同时输出细粒度事件（start/addr/data）与传输级汇总（i2c.transfer）。
"""

from __future__ import annotations

from typing import Any

from ....shared.waves import DigitalWave
from ...events import I2cEvent
from ...graph import Param
from ...registry import register

T_BUF = 1.3e-6  # 标准总线空闲时间


@register
class I2cDecodeNode:
    TYPE = "i2c_decode"
    INPUTS = {"in": "digital"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "scl": Param("str", default="", doc="SCL 通道名（空 = 第 1 通道）"),
        "sda": Param("str", default="", doc="SDA 通道名（空 = 第 2 通道）"),
        "stretch_warn_s": Param("float_pos", default=1e-3, doc="SCL 低电平持续超过该值告警（秒）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        wave: DigitalWave = inputs["in"]
        names = list(wave.channels)
        scl = params["scl"] or (names[0] if names else "")
        sda = params["sda"] or (names[1] if len(names) > 1 else scl)
        for n in (scl, sda):
            if n not in names:
                raise ValueError(f"通道 {n!r} 不存在；可用: {names}")
        if scl == sda:
            raise ValueError("SCL 与 SDA 不能是同一通道")

        scl_t, scl_lv = wave.edge_stream(scl)
        sda_t, sda_lv = wave.edge_stream(sda)
        scl_rises = scl_t[scl_lv == 1]
        scl_falls = scl_t[scl_lv == 0]

        events: list[I2cEvent] = []

        # 状态
        in_transfer = False
        tr: dict[str, Any] = {}
        bitcount = 0
        shift = 0
        byte_t0 = 0.0
        addr_phase = False
        addr_state = "first"
        last_stop_t: float | None = None

        def sda_at(t: float) -> int:
            return wave.level_at(sda, t)

        def scl_at(t: float) -> int:
            return wave.level_at(scl, t)

        # SDA 边沿 → START/STOP 判定
        sda_events: list[tuple[float, int]] = list(zip(sda_t, sda_lv))
        # SCL 上升沿 → 位采样；两者按时间归并（同时刻 SDA 判定先行）
        merged: list[tuple[float, str, Any]] = [
            (float(t), "sda", int(lv)) for t, lv in sda_events
        ] + [
            (float(t), "scl_rise", None) for t in scl_rises
        ]
        merged.sort(key=lambda e: (e[0], 0 if e[1] == "sda" else 1))

        fall_idx = 0
        for t, etype, payload in merged:
            if etype == "sda":
                after = payload
                if scl_at(t) != 1:
                    continue  # SCL 低时 SDA 变化 = 正常数据
                if after == 0:  # START / 重复 START
                    if last_stop_t is not None and t - last_stop_t < T_BUF:
                        events.append(I2cEvent("i2c.warn", last_stop_t, t, "总线空闲违例",
                                               errors=["bus-free"], ann_class="warn"))
                    if in_transfer:
                        events.append(I2cEvent("i2c.repeat-start", t, t, "Sr", ann_class="start"))
                    else:
                        events.append(I2cEvent("i2c.start", t, t, "S", ann_class="start"))
                        in_transfer = True
                        tr = {"t0": t, "addr": None, "read": None, "is10": False,
                              "bytes": [], "acks": []}
                    bitcount, shift = 0, 0
                    addr_phase, addr_state = True, "first"
                else:  # STOP
                    if in_transfer:
                        label = _transfer_label(tr)
                        errs = []
                        if tr["addr"] is None:
                            errs.append("no-address")
                        if any(not a for a in tr["acks"]):
                            errs.append("nack")
                        events.append(I2cEvent(
                            "i2c.transfer", tr["t0"], t, label, errors=errs,
                            ann_class=("err" if errs else "data"),
                            address=tr["addr"], is_10bit=tr["is10"], read=tr["read"],
                            data_bytes=list(tr["bytes"]), acks=list(tr["acks"]),
                        ))
                        in_transfer = False
                        addr_phase = False
                        events.append(I2cEvent("i2c.stop", t, t, "P", ann_class="stop"))
                    else:
                        events.append(I2cEvent("i2c.stop", t, t, "P(孤立)", errors=["spurious"],
                                               ann_class="warn"))
                    last_stop_t = t
                continue

            # SCL 上升沿：位采样
            while fall_idx < len(scl_falls) and scl_falls[fall_idx] < t:
                ft = float(scl_falls[fall_idx])
                if t - ft > params["stretch_warn_s"]:
                    events.append(I2cEvent("i2c.warn", ft, t, "时钟拉伸",
                                           errors=["clock-stretch"], ann_class="warn"))
                fall_idx += 1
            if not in_transfer:
                continue
            if bitcount < 8:
                if bitcount == 0:
                    byte_t0 = t
                shift = (shift << 1) | sda_at(t)
                bitcount += 1
            else:
                ack = sda_at(t) == 0
                byte = shift
                if addr_phase:
                    tr["acks"].append(ack)
                    if addr_state == "first":
                        if (byte & 0xF8) == 0xF0:
                            tr["addr"] = (byte & 0x06) << 7
                            tr["read"] = bool(byte & 1)
                            tr["is10"] = True
                            addr_state = "second"
                        else:
                            tr["addr"] = byte >> 1
                            tr["read"] = bool(byte & 1)
                            tr["is10"] = False
                            addr_phase = False
                            events.append(I2cEvent(
                                "i2c.addr", byte_t0, t,
                                f"0x{tr['addr']:02X} {'R' if tr['read'] else 'W'}",
                                ann_class="start", address=tr["addr"], read=tr["read"],
                            ))
                    else:
                        tr["addr"] |= byte
                        addr_phase = False
                        events.append(I2cEvent(
                            "i2c.addr", byte_t0, t,
                            f"0x{tr['addr']:03X} {'R' if tr['read'] else 'W'} (10bit)",
                            ann_class="start", address=tr["addr"], is_10bit=True, read=tr["read"],
                        ))
                else:
                    tr["bytes"].append(byte)
                    tr["acks"].append(ack)
                    events.append(I2cEvent(
                        "i2c.data", byte_t0, t,
                        f"0x{byte:02X} ({'ACK' if ack else 'NAK'})",
                        errors=([] if ack else []),
                        ann_class=("ack" if ack else "warn"),
                        byte_index=len(tr["bytes"]) - 1,
                    ))
                bitcount, shift = 0, 0

        # 采集结束仍未闭合的传输 → 汇总事件 + truncated
        if in_transfer:
            label = _transfer_label(tr)
            events.append(I2cEvent(
                "i2c.transfer", tr["t0"], wave.t_end, label,
                errors=["truncated"] + (["no-address"] if tr["addr"] is None else []),
                ann_class="err", address=tr["addr"], is_10bit=tr["is10"], read=tr["read"],
                data_bytes=list(tr["bytes"]), acks=list(tr["acks"]),
            ))
        # 全局时间有序（发布语言不变量）
        events.sort(key=lambda e: (e.t_start, e.t_end))
        return {"out": events}


def _transfer_label(tr: dict) -> str:
    if tr["addr"] is None:
        return f"?[{_hex(tr['bytes'])}]"
    rw = "R" if tr["read"] else "W"
    a = f"0x{tr['addr']:03X}" if tr["is10"] else f"0x{tr['addr']:02X}"
    acks = ",".join("A" if a_ else "N" for a_ in tr["acks"]) or "-"
    return f"{rw} {a} [{_hex(tr['bytes'])}] {acks}"


def _hex(bytes_: list[int]) -> str:
    return " ".join(f"{b:02X}" for b in bytes_)
