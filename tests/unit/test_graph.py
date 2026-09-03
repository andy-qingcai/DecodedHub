"""图引擎单测：五规则验证 + 拉式记忆化求值。"""

import numpy as np
import pytest

from decodehub.decode import Graph, evaluate, validate, get_registry
from decodehub.decode.synth import encode_uart
from decodehub.shared import Capture, CaptureMeta, GraphValidationError, NodeError


def _capture_with_uart(bytes_=b"\x55", baud=9600):
    wave = encode_uart(bytes_, baud=baud)
    return Capture(meta=CaptureMeta(source_kind="synth", format_key="synth"), digital=wave)


def _simple_graph():
    g = Graph()
    g.add_node("pick", "digital_pick")
    g.add_node("u", "uart_decode", baud=9600, rx="TX")
    g.add_edge("pick", "out", "u", "in")
    return g


class TestValidation:
    def test_ok(self):
        validate(_simple_graph(), get_registry())

    def test_unknown_node_type(self):
        g = Graph()
        g.add_node("x", "nope_node")
        with pytest.raises(GraphValidationError, match="未注册"):
            validate(g, get_registry())

    def test_missing_port(self):
        g = Graph()
        g.add_node("pick", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600)
        g.add_edge("pick", "nonexistent", "u", "in")
        with pytest.raises(GraphValidationError, match="无输出端口"):
            validate(g, get_registry())

    def test_type_mismatch(self):
        g = Graph()
        g.add_node("pick", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600)
        g.add_edge("pick", "out", "u", "nonexistent_port")
        with pytest.raises(GraphValidationError):
            validate(g, get_registry())

    def test_cycle(self):
        g = Graph()
        g.add_node("f1", "event_filter")
        g.add_node("f2", "event_filter")
        g.add_edge("f1", "out", "f2", "in")
        g.add_edge("f2", "out", "f1", "in")
        with pytest.raises(GraphValidationError, match="环"):
            validate(g, get_registry())

    def test_double_writer(self):
        g = Graph()
        g.add_node("p1", "digital_pick")
        g.add_node("p2", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600)
        g.add_edge("p1", "out", "u", "in")
        g.add_edge("p2", "out", "u", "in")
        with pytest.raises(GraphValidationError, match="多条入边"):
            validate(g, get_registry())

    def test_unknown_param(self):
        g = Graph()
        g.add_node("pick", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600, wrong_param=1)
        g.add_edge("pick", "out", "u", "in")
        with pytest.raises(GraphValidationError, match="未知参数"):
            validate(g, get_registry())

    def test_param_range(self):
        g = Graph()
        g.add_node("pick", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600, data_bits=10)  # >9
        g.add_edge("pick", "out", "u", "in")
        with pytest.raises(GraphValidationError, match="参数校验失败"):
            validate(g, get_registry())


class TestEvaluate:
    def test_basic_and_cache(self):
        g = _simple_graph()
        cap = _capture_with_uart(b"\x55\xAA")
        memo = evaluate(g, get_registry(), ["u"], sources={"pick": {"in": cap}})
        events = memo["u"]["out"]
        assert [e.value for e in events] == [0x55, 0xAA]
        assert "pick" in memo and "u" in memo
        # 只算目标祖先：无多余节点
        assert set(memo) == {"pick", "u"}

    def test_missing_input(self):
        g = Graph()
        g.add_node("u", "uart_decode", baud=9600)
        with pytest.raises(NodeError, match="缺少输入"):
            evaluate(g, get_registry(), ["u"], sources={})

    def test_node_error_context(self):
        g = Graph()
        g.add_node("pick", "digital_pick", channels=["NOPE"])
        g.add_node("u", "uart_decode", baud=9600)
        g.add_edge("pick", "out", "u", "in")
        cap = _capture_with_uart()
        with pytest.raises(NodeError, match="pick"):
            evaluate(g, get_registry(), ["u"], sources={"pick": {"in": cap}})

    def test_diamond_graph(self):
        # pick → uart → filter；pick → 另一 filter（扇出）
        g = Graph()
        g.add_node("pick", "digital_pick")
        g.add_node("u", "uart_decode", baud=9600)
        g.add_node("f1", "event_filter", kinds=["uart.frame"])
        g.add_node("f2", "event_filter", has_errors=True)
        g.add_edge("pick", "out", "u", "in")
        g.add_edge("u", "out", "f1", "in")
        g.add_edge("u", "out", "f2", "in")
        validate(g, get_registry())
        cap = _capture_with_uart(b"\x01\x02")
        memo = evaluate(g, get_registry(), ["f1", "f2"], sources={"pick": {"in": cap}})
        assert len(memo["f1"]["out"]) == 2
        assert memo["f2"]["out"] == []
        # u 只求值一次（记忆化）
        assert set(memo) == {"pick", "u", "f1", "f2"}
