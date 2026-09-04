"""field_split 节点测试：事件流 → 字段切分事件 + 呈现注册。"""

import dataclasses

import pytest

from decodehub.decode.events import I2cEvent, UartEvent
from decodehub.decode.fields import (
    FieldSetEvent,
    FieldSpecError,
    register_fields,
)
from decodehub.decode.graph import Graph, evaluate, validate
from decodehub.decode.nodes.field_split import FieldSplitNode
from decodehub.decode.presentation import presentation_of
from decodehub.decode.registry import NODE_REGISTRY

SPEC = {"seq": [{"id": "cmd", "type": "u8"}, {"id": "arg", "type": "u16"}]}
PARAMS = {"spec": SPEC, "spec_name": "", "protocol": "", "kinds": []}


def _transfer(data: list[int], t0: float = 1.0) -> I2cEvent:
    return I2cEvent("i2c.transfer", t0, t0 + 0.001, "W 0x51", data_bytes=data)


def test_node_registered_with_contract():
    cls = NODE_REGISTRY["field_split"]
    assert cls.INPUTS == {"in": "events"}
    assert cls.OUTPUTS == {"out": "events"}


def test_split_appends_fieldset_event_after_source():
    events = [I2cEvent("i2c.start", 0.9, 0.9, "S"), _transfer([0x01, 0x00, 0xFF])]
    out = FieldSplitNode().run({"in": events}, dict(PARAMS))["out"]
    assert len(out) == 3  # 原事件保留，FieldSetEvent 紧随其源之后
    fse = out[2]
    assert isinstance(fse, FieldSetEvent) and fse.kind == "fields.split"
    assert fse.t_start == 1.0 and fse.source_kind == "i2c.transfer"
    cmd, arg = fse.fields
    assert (cmd.value, arg.value) == (0x01, 0x00FF)
    # 原事件不被替换（原始帧保留，过滤交给 event_filter）
    assert out[0].kind == "i2c.start" and out[1].kind == "i2c.transfer"


def test_spec_name_lookup_from_registry():
    register_fields("test.cmdspec", SPEC)
    params = dict(PARAMS, spec=None, spec_name="test.cmdspec")
    out = FieldSplitNode().run({"in": [_transfer([0x02, 0x00, 0x01])]}, params)["out"]
    fse = [e for e in out if isinstance(e, FieldSetEvent)][0]
    assert fse.fields[0].value == 0x02 and fse.spec == "test.cmdspec"


def test_missing_spec_raises():
    events = [_transfer([0x01])]
    with pytest.raises(FieldSpecError):
        FieldSplitNode().run({"in": events},
                             dict(PARAMS, spec=None, spec_name=""))


def test_bad_spec_raises_field_spec_error():
    with pytest.raises(FieldSpecError):
        FieldSplitNode().run({"in": [_transfer([0x01])]},
                             dict(PARAMS, spec={"seq": [{"id": "x", "type": "u99"}]}))


def test_auto_protocol_detect_and_explicit_override():
    uart = UartEvent("uart.frame", 2.0, 2.001, "0x41", value=0x41, data_bits=8)
    out = FieldSplitNode().run(
        {"in": [uart]},
        dict(PARAMS, spec={"seq": [{"id": "b", "type": "u8"}]}),
    )["out"]
    assert out[1].fields[0].value == 0x41  # 自动按 kind 前缀走 uart 提取器

    # 强制 i2c 提取器 → uart 事件提不出载荷 → 不产生切分事件
    out2 = FieldSplitNode().run(
        {"in": [uart]}, dict(PARAMS, spec={"seq": [{"id": "b", "type": "u8"}]},
                             protocol="i2c"))["out"]
    assert all(not isinstance(e, FieldSetEvent) for e in out2)


def test_kinds_filter():
    events = [_transfer([0x01]), I2cEvent("i2c.data", 1.0005, 1.0008, "0x01")]
    out = FieldSplitNode().run({"in": events}, dict(PARAMS, kinds=["i2c.transfer"]))["out"]
    assert [e.kind for e in out] == ["i2c.transfer", "fields.split", "i2c.data"]


def test_fieldset_event_serializes_via_asdict():
    out = FieldSplitNode().run({"in": [_transfer([0x01, 0x00, 0x02])]}, dict(PARAMS))["out"]
    d = out[1].to_dict()
    assert d["kind"] == "fields.split"
    assert d["fields"][0]["id"] == "cmd"
    assert d["fields"][1]["value"] == 2


def test_truncated_payload_records_error_not_exception():
    out = FieldSplitNode().run({"in": [_transfer([0x05, 0x01])]}, dict(PARAMS))["out"]
    fse = out[1]
    assert "truncated" in fse.fields[1].errors


def test_presentation_registered_for_fields_family():
    p = presentation_of("fields.split")
    assert p is not None and p.protocol == "fields"
    assert p.plot_family is False                     # 不往时序图里添乱
    assert "fields.split" in p.preview_kinds
    out = FieldSplitNode().run({"in": [_transfer([0x01, 0x00, 0x02])]}, dict(PARAMS))["out"]
    text = p.detail_fn(out[1])
    assert "cmd=0x01" in text and "arg=0x0002" in text


def test_works_inside_graph_with_param_coercion():
    g = Graph()
    g.add_node("fs", "field_split", spec=SPEC)
    g.add_node("sink", "event_filter")
    g.add_edge("fs", "out", "sink", "in")
    validate(g, NODE_REGISTRY)
    memo = evaluate(g, NODE_REGISTRY, targets=["sink"],
                    sources={"fs": {"in": [_transfer([0x01, 0x00, 0x02])]}})
    out = memo["fs"]["out"]
    assert any(isinstance(e, FieldSetEvent) for e in out)


def test_spec_as_json_string_accepted():
    """MCP 传输层以字符串承载 JSON：内联规格字符串应被宽容解析。"""
    import json as _json
    out = FieldSplitNode().run({"in": [_transfer([0x01, 0x00, 0x02])]},
                               dict(PARAMS, spec=_json.dumps(SPEC)))["out"]
    fse = [e for e in out if isinstance(e, FieldSetEvent)][0]
    assert fse.fields[0].value == 0x01
