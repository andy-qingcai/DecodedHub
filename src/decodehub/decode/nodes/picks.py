"""通道挑选节点：Capture → 数字子集 / 模拟通道列表。"""

from __future__ import annotations

from typing import Any

from ...shared.errors import NodeError
from ...shared.waves import AnalogChannel, Capture, DigitalWave
from ..graph import Param
from ..registry import register


@register
class DigitalPickNode:
    TYPE = "digital_pick"
    INPUTS = {"in": "capture"}
    OUTPUTS = {"out": "digital"}
    PARAMS = {
        "channels": Param("str_list", default=[], doc="要抽取的数字通道名；空 = 全部"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        cap: Capture = inputs["in"]
        if cap.digital is None:
            raise NodeError("digital_pick", "采集不含数字通道（只有模拟）", "analog only")
        names = params["channels"] or list(cap.digital.channels)
        try:
            wave: DigitalWave = cap.digital.select(names)
        except KeyError as e:
            raise NodeError("digital_pick", str(e), f"可用通道: {list(cap.digital.channels)}") from e
        return {"out": wave}


@register
class AnalogPickNode:
    TYPE = "analog_pick"
    INPUTS = {"in": "capture"}
    OUTPUTS = {"out": "analog"}
    PARAMS = {
        "channels": Param("str_list", default=[], doc="要抽取的模拟通道名；空 = 全部"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        cap: Capture = inputs["in"]
        if not cap.analog:
            raise NodeError("analog_pick", "采集不含模拟通道", "digital only")
        wanted = params["channels"]
        if not wanted:
            return {"out": list(cap.analog)}
        by_name = {ch.name: ch for ch in cap.analog}
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise NodeError(
                "analog_pick", f"模拟通道不存在: {missing}", f"可用: {list(by_name)}"
            )
        return {"out": [by_name[n] for n in wanted] if wanted else list(cap.analog)}
