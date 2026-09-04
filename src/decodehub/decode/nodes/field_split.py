"""字段切分节点：events → events（原帧保留，切分事件紧随其源插入）。

协议帧的 payload 再解析不在各协议解码器里硬编码，而是用统一的字段规格
（decode.fields 六原语）描述、同一套算法结算（ADR-016）。规格来源二选一：
内联 dict（spec，可经 MCP/JSON 下发）或具名规格（spec_name，register_fields）。
载荷提取按协议族分派（i2c/uart/spi/uplink/downlink，缺省按事件 kind 前缀自动）。
"""

from __future__ import annotations

from typing import Any

from ..fields import FieldSetEvent, FieldSpecError, resolve_spec, split_one
from ..graph import Param
from ..registry import register


@register
class FieldSplitNode:
    TYPE = "field_split"
    INPUTS = {"in": "events"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "spec": Param("any", default=None, doc="内联字段规格（dict，六原语）；优先于 spec_name"),
        "spec_name": Param("str", default="", doc="已注册规格名（register_fields）"),
        "protocol": Param("str", default="", doc="载荷提取协议族（i2c/uart/spi/uplink/downlink）；空 = 按事件 kind 前缀自动"),
        "kinds": Param("str_list", default=[], doc="只切分这些事件 kind；空 = 全部带载荷事件"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        events = inputs["in"]
        spec_param = params["spec"]
        if spec_param is None and not params["spec_name"]:
            raise FieldSpecError(
                "field_split 需要 spec（内联规格 dict）或 spec_name（已注册规格）之一")
        compiled, label = resolve_spec(spec_param if spec_param is not None
                                        else params["spec_name"])
        kinds = params["kinds"]
        protocol = params["protocol"]
        out: list[Any] = []
        for ev in events:
            out.append(ev)
            if kinds and ev.kind not in kinds:
                continue
            fse = split_one(ev, compiled, label, protocol)
            if fse is not None:
                out.append(fse)
        return {"out": out}
