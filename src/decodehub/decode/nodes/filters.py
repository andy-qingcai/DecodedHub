"""事件过滤节点：时间窗 / 类型 / 错误。"""

from __future__ import annotations

from typing import Any

from ..events import DecodedEvent
from ..graph import Param
from ..registry import register


@register
class EventFilterNode:
    TYPE = "event_filter"
    INPUTS = {"in": "events"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "kinds": Param("str_list", default=[], doc="保留的事件 kind；空 = 全部"),
        "t_min": Param("float", default=None, doc="时间窗下界（秒）"),
        "t_max": Param("float", default=None, doc="时间窗上界（秒）"),
        "has_errors": Param("bool", default=None, doc="True=只看错误事件；False=只看干净事件；None=全部"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        events: list[DecodedEvent] = inputs["in"]
        out = []
        for ev in events:
            if params["kinds"] and ev.kind not in params["kinds"]:
                continue
            if params["t_min"] is not None and ev.t_start < params["t_min"]:
                continue
            if params["t_max"] is not None and ev.t_start > params["t_max"]:
                continue
            if params["has_errors"] is True and not ev.errors:
                continue
            if params["has_errors"] is False and ev.errors:
                continue
            out.append(ev)
        return {"out": out}
