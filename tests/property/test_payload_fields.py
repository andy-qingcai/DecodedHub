"""端到端：synth 合成 I2C 流量 → 真实图（pick → i2c_decode → field_split）→ 字段树。"""

from decodehub.decode.fields import FieldSetEvent
from decodehub.decode.graph import Graph, evaluate, validate
from decodehub.decode.registry import NODE_REGISTRY
from decodehub.decode.synth import encode_i2c
from decodehub.shared.waves import Capture, CaptureMeta

SPEC = {
    "seq": [
        {"id": "cmd", "type": "u8", "enum": {"3": "READ_SENSOR", "4": "WRITE_REG"}},
        {"id": "n", "type": "u16"},
        {"id": "body", "type": "bytes", "size": "n"},
    ],
}


def _capture(wave) -> Capture:
    return Capture(meta=CaptureMeta(source_kind="kingst", format_key="kingst_csv"),
                   digital=wave)


def _run_field_split(data: list[int], spec=SPEC):
    wave = encode_i2c([{"addr": 0x51, "read": False, "data": data}])
    g = Graph()
    g.add_node("pick", "digital_pick")
    g.add_node("i2c", "i2c_decode")
    g.add_node("fs", "field_split", spec=spec)
    g.add_edge("pick", "out", "i2c", "in")
    g.add_edge("i2c", "out", "fs", "in")
    validate(g, NODE_REGISTRY)
    memo = evaluate(g, NODE_REGISTRY, targets=["fs"],
                    sources={"pick": {"in": _capture(wave)}})
    return [e for e in memo["fs"]["out"] if isinstance(e, FieldSetEvent)]


def test_e2e_dynamic_length_payload_split():
    fss = _run_field_split([0x03, 0x00, 0x02, 0xAB, 0xCD])
    assert len(fss) == 1  # 每个带载荷的 i2c.transfer 恰好一个切分事件
    cmd, n, body = fss[0].fields
    assert cmd.value == 0x03 and cmd.enum_label == "READ_SENSOR"
    assert n.value == 2
    assert body.value == b"\xab\xcd"


def test_e2e_multiple_transfers_order_preserved():
    from decodehub.decode.synth import encode_i2c as enc
    from decodehub.decode.graph import Graph as G
    from decodehub.shared.waves import Capture as C, CaptureMeta as M

    wave = enc([
        {"addr": 0x51, "read": False, "data": [0x03, 0x00, 0x01, 0xAA]},
        {"addr": 0x51, "read": False, "data": [0x04, 0x00, 0x01, 0xBB]},
    ])
    g = G()
    g.add_node("pick", "digital_pick")
    g.add_node("i2c", "i2c_decode")
    g.add_node("fs", "field_split", spec=SPEC)
    g.add_edge("pick", "out", "i2c", "in")
    g.add_edge("i2c", "out", "fs", "in")
    validate(g, NODE_REGISTRY)
    memo = evaluate(g, NODE_REGISTRY, targets=["fs"],
                    sources={"pick": {"in": C(meta=M(source_kind="kingst",
                                                       format_key="kingst_csv"),
                                              digital=wave)}})
    fss = [e for e in memo["fs"]["out"] if isinstance(e, FieldSetEvent)]
    assert len(fss) == 2
    assert fss[0].fields[0].enum_label == "READ_SENSOR"
    assert fss[1].fields[0].enum_label == "WRITE_REG"
    # 全局时间有序不变量保持
    ts = [(e.t_start, e.t_end) for e in memo["fs"]["out"]]
    assert ts == sorted(ts, key=lambda p: (p[0], p[1]))
