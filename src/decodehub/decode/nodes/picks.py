"""通道挑选节点：Capture → 数字子集 / 模拟通道列表。

节点实现只抛领域异常（ValueError/KeyError）——NodeError 由图引擎统一包装
并注入 spec 节点 id（节点不知道自己在图中的 id，见 docs/30 错误模型）。
"""

from __future__ import annotations

from typing import Any

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
            raise ValueError("采集不含数字通道（只有模拟）")
        names = params["channels"] or list(cap.digital.channels)
        wave: DigitalWave = cap.digital.select(names)  # KeyError 带可用通道表
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
            raise ValueError("采集不含模拟通道")
        wanted = params["channels"]
        if not wanted:
            return {"out": list(cap.analog)}
        by_name = {ch.name: ch for ch in cap.analog}
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise ValueError(f"模拟通道不存在: {missing}；可用: {list(by_name)}")
        return {"out": [by_name[n] for n in wanted] if wanted else list(cap.analog)}
