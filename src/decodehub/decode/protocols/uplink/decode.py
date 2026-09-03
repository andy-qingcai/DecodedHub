"""上行 DSSS 图节点（ADR-010）：原始模拟数据沿图传递的示范路径。

    apick(analog) → uplink_precond(analog→analog) → uplink_decode(analog→events)

与 UART/I2C/SPI 的本质差异：上行是扩频 DPSK 式突发，解调需要**幅度与相位信息**
（PN 相关、软符号值），一位的 slicer 切片会毁掉全部信息——因此模拟通道直接
流入解码节点，不经任何数字化节点（这正是图端口类型系统的意义：`analog` 与
`digital` 是两种一等数据，模拟→数字的唯一路径是 slicer，而模拟→解码器合法）。

- uplink_precond: 抽取到 ~12 样点/chip + 1ms 滑动均值 HPF 剥离 60Hz 包络
                  （匹配滤波前必须先去包络——在原始波形上相关会锁住包络）。
- uplink_decode:  vendored 接收机（PN 相关 → 梳齿符号 → 能量分段 → 帧同步 →
                  码片速率仲裁）。纯噪声/无突发 → 诚实拒绝（warn 事件，非伪帧）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ....shared.waves import AnalogChannel
from ...events import UplinkEvent
from ...graph import Param
from ...registry import register
from .._shared import pick_channel as _pick_channel, require_uniform as _require_uniform
from .dsss import UplinkConfig, decode_uplink, precondition, uplink_profile

_COMMON_PARAMS = {
    "channel": Param("str", default="", doc="模拟通道名（空 = 第一个）"),
    "profile": Param("str", default="default", doc="协议档案名（新 PN/帧形扩展点）"),
    "chip_s": Param("float_pos", default=None, doc="标称码片周期秒（缺省 1e-6；"
                   "实测约 0.9692e-6，接收机自动估计，一般无需设置）"),
}



def _build_cfg(params: dict[str, Any]) -> UplinkConfig:
    over: dict[str, Any] = {}
    if params.get("chip_s"):
        over["chip_s"] = float(params["chip_s"])
    try:
        return uplink_profile(params.get("profile") or "default", **over)
    except KeyError as e:
        raise ValueError(f"未知上行协议档案: {e}") from e


@register
class UplinkPrecondNode:
    TYPE = "uplink_precond"
    INPUTS = {"in": "analog"}
    OUTPUTS = {"out": "analog"}
    PARAMS = _COMMON_PARAMS

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        ch = _pick_channel(inputs["in"], params.get("channel") or "")
        cfg = _build_cfg(params)
        y, dt = _require_uniform(ch)
        fs = 1.0 / dt
        y2, fs2 = precondition(y, fs, cfg)
        out = AnalogChannel(
            name=ch.name, samples=y2.astype(np.float32), units=ch.units,
            t0=ch.t0, dt=1.0 / fs2, raw_scale=ch.raw_scale, raw_offset=ch.raw_offset,
        )
        return {"out": [out]}


@register
class UplinkDecodeNode:
    TYPE = "uplink_decode"
    INPUTS = {"in": "analog"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        **_COMMON_PARAMS,
        "invert": Param("bool", default=False, doc="物理极性反相"),
        "unipolar": Param("bool", default=False, doc="码片 0/+A 编码（缺省 -A/+A 双极性）"),
        "msb_first": Param("bool", default=True, doc="PN 字高位在先"),
        # 协议形状全量可配（ADR-011；默认值 = "default" 档案，并非协议常量）
        "pn_word": Param("hexint", default=None,
                         doc="PN 扩频字（默认 0x3DA60E45；接受 0x 前缀）"),
        "pn_len": Param("int", default=None, lo=4, hi=1023,
                        doc="PN 码片数（默认 31）"),
        "pream": Param("bits", default=None,
                       doc="前导位串（默认 \"001\"）"),
        "data_bits": Param("int", default=None, lo=1, hi=32,
                           doc="每帧数据位数（默认 5）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        ch = _pick_channel(inputs["in"], params.get("channel") or "")
        over: dict[str, Any] = {}
        if params.get("chip_s"):
            over["chip_s"] = float(params["chip_s"])
        if params.get("invert"):
            over["invert_polarity"] = True
        if params.get("unipolar"):
            over["chip_mapping"] = "unipolar"
        over["msb_first"] = bool(params.get("msb_first", True))
        if params.get("pn_word") is not None:
            over["pn_word"] = int(params["pn_word"])
        if params.get("pn_len"):
            over["pn_len"] = int(params["pn_len"])
        if params.get("pream"):
            over["pream_bits"] = tuple(int(c) for c in str(params["pream"]))
        if params.get("data_bits"):
            over["n_data_bits"] = int(params["data_bits"])
        try:
            cfg = uplink_profile(params.get("profile") or "default", **over)
        except KeyError as e:
            raise ValueError(f"未知上行协议档案: {e}") from e
        y, dt = _require_uniform(ch)
        res = decode_uplink(y, 1.0 / dt, cfg)

        t0_off = float(ch.t0)  # 事件时刻归一到采集时间轴（MHO98 触发居中为负时间）
        events: list[UplinkEvent] = []
        for w in res.warnings:
            events.append(UplinkEvent("uplink.warn", t0_off, t0_off, w[:120],
                                      errors=["warn"], ann_class="warn"))
        for fr in res.frames:
            t0 = float(fr.t_start) + t0_off
            events.append(UplinkEvent(
                "uplink.frame", t0, t0 + cfg.frame_s,
                f"{fr.data_hex} conf {fr.mean_conf:.2f}"
                + ("" if fr.pream_ok else " ✗前导"),
                errors=([] if fr.pream_ok else ["preamble"]),
                ann_class=("data" if fr.pream_ok else "err"),
                value=int(fr.data_hex, 16), data_bits=list(fr.data_bits),
                pream_ok=fr.pream_ok, confidence=fr.mean_conf, burst=fr.burst,
            ))
        events.sort(key=lambda e: (e.t_start, e.t_end))
        return {"out": events}
