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
        # 节点抛领域异常,引擎包装为 NodeError 且 node_id = spec id（非类型名）
        with pytest.raises(NodeError) as ei:
            evaluate(g, get_registry(), ["u"], sources={"pick": {"in": cap}})
        assert ei.value.node_id == "pick"
        assert "NOPE" in str(ei.value) and "可用" in str(ei.value)

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


class TestMemoReuse:
    """跨 run memo 复用（docs/30：改参数重解码只重算参数变化的节点）。"""

    def _counting_registry(self, calls):
        reg = dict(get_registry())

        class CountingPick:
            TYPE = "counting_pick"
            INPUTS = {"in": "capture"}
            OUTPUTS = {"out": "digital"}
            PARAMS = {}

            def run(self, inputs, params):
                calls.append(1)
                return {"out": inputs["in"].digital}

        reg["counting_pick"] = CountingPick
        return reg

    def test_passed_memo_hits_without_rerun(self):
        calls = []
        reg = self._counting_registry(calls)
        g = Graph()
        g.add_node("p", "counting_pick")
        cap = _capture_with_uart()

        memo = evaluate(g, reg, ["p"], sources={"p": {"in": cap}})
        assert len(calls) == 1

        memo2 = evaluate(g, reg, ["p"], sources={"p": {"in": cap}}, memo=memo)
        assert len(calls) == 1  # 命中缓存,未重算
        assert memo2 is memo  # 返回值即缓存字典,可回存

    def test_fresh_memo_reruns(self):
        calls = []
        reg = self._counting_registry(calls)
        g = Graph()
        g.add_node("p", "counting_pick")
        cap = _capture_with_uart()
        evaluate(g, reg, ["p"], sources={"p": {"in": cap}})
        evaluate(g, reg, ["p"], sources={"p": {"in": cap}})  # 不传 memo → 重算
        assert len(calls) == 2


class TestParamDefaults:
    def test_mutable_default_not_shared(self):
        from decodehub.decode.graph import NodeSpec, _validate_params
        from decodehub.decode.nodes.picks import DigitalPickNode
        a = _validate_params(NodeSpec(id="a", type="digital_pick"), DigitalPickNode)
        b = _validate_params(NodeSpec(id="b", type="digital_pick"), DigitalPickNode)
        assert a["channels"] == [] and b["channels"] == []
        assert a["channels"] is not b["channels"]  # 逐次物化,不共享实例
        a["channels"].append("X")  # 污染不外溢
        assert _validate_params(NodeSpec(id="c", type="digital_pick"), DigitalPickNode)["channels"] == []


class TestRegisterContract:
    def test_rejects_incomplete_node(self):
        from decodehub.decode.registry import register

        class NoPorts:
            TYPE = "bad_no_ports"

        with pytest.raises(ValueError, match="INPUTS"):
            register(NoPorts)

        class NoRun:
            TYPE = "bad_no_run"
            INPUTS = {"in": "capture"}
            OUTPUTS = {"out": "digital"}
            PARAMS = {}

        with pytest.raises(ValueError, match="run"):
            register(NoRun)

    def test_rejects_duplicate_type(self):
        from decodehub.decode.registry import register
        from decodehub.decode.nodes.picks import DigitalPickNode

        with pytest.raises(ValueError, match="重复注册"):
            register(DigitalPickNode)
