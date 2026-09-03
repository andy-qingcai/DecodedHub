"""下行 DBPSK 图节点（ADR-011）：跨节点扇入——上行帧作为同步锚。

    apick_dl(analog) ──────────────┐
                                   ▼
    [上行子图] … → uplink_decode:out ──▶ downlink_decode(in: analog, sync: events)

下行槽位挂在上行 60Hz 帧网格上（delta 自校准，绝不假设），因此解码器需要
**另一个解码器的输出**（上行帧起点）+ 本源原始模拟——这是图扇入的教科书场景：
events 与 analog 两条数据流在一个节点汇合。

协议参数全量可配（用户要求，ADR-011）：载波/每 bit 周期数/包长/槽位偏移/帧率/
profile 均为节点参数；默认值 = "default" 档案（原实机调参值），并非协议常量。
"""

from __future__ import annotations

from typing import Any

from ....shared.waves import AnalogChannel
from ...events import DecodedEvent, DownlinkEvent
from ...graph import Param
from ...registry import register
from .dpsk import decode_downlink, downlink_profile
from .._shared import pick_channel as _pick_channel, require_uniform as _require_uniform


@register
class DownlinkDecodeNode:
    TYPE = "downlink_decode"
    INPUTS = {"in": "analog", "sync": "events"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "channel": Param("str", default="", doc="下行模拟通道名（空 = 第一个）"),
        "profile": Param("str", default="default", doc="下行协议档案名"),
        "fc_nominal": Param("float_pos", default=None,
                            doc="标称载波 Hz（默认 263e3）"),
        "cycles_per_bit": Param("int", default=None, lo=1, hi=1024,
                                doc="每 bit 载波周期数（默认 10）"),
        "n_bits": Param("int", default=None, lo=2, hi=1024,
                        doc="包符号数含起始位（默认 17 = 1 起始 + 16 数据）"),
        "slot_offsets_us": Param("float_list", default=None,
                                 doc="槽位偏移 µs 列表（默认固件值 1970/4748/…/15858）；"
                                     "数量即每帧槽数"),
        "frame_hz": Param("float_pos", default=None,
                          doc="上行帧网格频率 Hz（默认 60）"),
        "invert": Param("bool", default=False,
                        doc="差分极性反转（1=翻转 的解读取反）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        ch: AnalogChannel = _pick_channel(inputs["in"], params.get("channel") or "")
        sync: list[DecodedEvent] = inputs["sync"]
        frame_starts = sorted(e.t_start for e in sync if e.kind == "uplink.frame")
        if not frame_starts:
            raise ValueError(
                "sync 输入中没有上行帧（kind=uplink.frame）——下行解码以上行帧为锚，"
                "请确认上行子图已接入且解出帧"
            )
        y, dt = _require_uniform(ch)
        fs = 1.0 / dt

        over: dict[str, Any] = {}
        if params.get("fc_nominal"):
            over["fc_nominal"] = float(params["fc_nominal"])
        if params.get("cycles_per_bit"):
            over["cycles_per_bit"] = int(params["cycles_per_bit"])
        if params.get("n_bits"):
            over["n_bits"] = int(params["n_bits"])
        if params.get("slot_offsets_us"):
            over["slot_offsets_s"] = tuple(v * 1e-6
                                           for v in params["slot_offsets_us"])
        if params.get("frame_hz"):
            over["frame_period_s"] = 1.0 / float(params["frame_hz"])
        if params.get("invert"):
            over["invert_polarity"] = True
        try:
            cfg = downlink_profile(params.get("profile") or "default", **over)
        except KeyError as e:
            raise ValueError(f"未知下行协议档案: {e}") from e

        res = decode_downlink(y, fs, frame_starts, cfg)
        tb = cfg.cycles_per_bit / (res.fc_est or cfg.fc_nominal)
        events: list[DownlinkEvent] = []
        for w in res.warnings:
            events.append(DownlinkEvent("downlink.warn", float(ch.t0), float(ch.t0),
                                        w[:120], errors=["warn"], ann_class="warn"))
        for pk in res.packets:
            t0 = float(pk.t_start) + float(ch.t0)
            events.append(DownlinkEvent(
                "downlink.packet", t0, t0 + cfg.n_bits * tb,
                f"{pk.data_hex} 槽{pk.slot} conf {pk.mean_conf:.2f}",
                value=int(pk.data_hex, 16), value_inv=int(pk.data_hex_inv, 16),
                bits=list(pk.diff_bits), slot=pk.slot, frame=pk.frame,
                fc_est=pk.fc_est, confidence=pk.mean_conf,
            ))
        events.sort(key=lambda e: (e.t_start, e.t_end))
        return {"out": events}
