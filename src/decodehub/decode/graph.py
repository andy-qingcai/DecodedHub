"""图引擎（C3 核心）：类型化端口的 DAG + 拉式记忆化求值（ADR-002）。

- 构建期五规则验证（全部可检错误）：
  1) 边引用存在    2) 端口类型严格相等（无隐式转换）
  3) 输入端口至多一条入边    4) 无环（DFS 三色）    5) 参数经 PARAMS 校验
- 求值：递归下降即拓扑序；只计算目标的祖先；输出按节点 id 缓存。
- 外部输入（capture 等）经 `sources` 注入——图内没有文件源节点，保证纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..shared.errors import GraphValidationError, NodeError

PORT_TYPES = ("capture", "digital", "analog", "events", "scalar")


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, NodeSpec] = {}
        self.edges: list[Edge] = []

    def add_node(self, id: str, type: str, **params) -> "Graph":
        if id in self.nodes:
            raise GraphValidationError("edge-ref", f"节点 id 重复: {id}")
        self.nodes[id] = NodeSpec(id=id, type=type, params=params)
        return self

    def add_edge(self, src: str, src_port: str, dst: str, dst_port: str) -> "Graph":
        self.edges.append(Edge(src=src, src_port=src_port, dst=dst, dst_port=dst_port))
        return self

    def incoming(self, node_id: str) -> dict[str, tuple[str, str]]:
        return {e.dst_port: (e.src, e.src_port) for e in self.edges if e.dst == node_id}

    def to_text(self) -> str:
        lines = [f"节点 ({len(self.nodes)}):"]
        for n in self.nodes.values():
            ps = ", ".join(f"{k}={v!r}" for k, v in n.params.items()) or "-"
            lines.append(f"  [{n.id}] {n.type}({ps})")
        lines.append(f"边 ({len(self.edges)}):")
        for e in self.edges:
            lines.append(f"  {e.src}:{e.src_port} ──▶ {e.dst}:{e.dst_port}")
        return "\n".join(lines)


def validate(graph: Graph, registry: Mapping[str, type]) -> None:
    """构建期校验（五规则）。"""
    for e in graph.edges:
        if e.src not in graph.nodes or e.dst not in graph.nodes:
            raise GraphValidationError(
                "edge-ref", f"边引用了不存在的节点: {e.src} → {e.dst}"
            )
    for n in graph.nodes.values():
        if n.type not in registry:
            raise GraphValidationError("edge-ref", f"节点 {n.id} 的类型未注册: {n.type}")
        node_cls = registry[n.type]
        outs = node_cls.OUTPUTS
        ins = node_cls.INPUTS
        for e in graph.edges:
            if e.src == n.id and e.src_port not in outs:
                raise GraphValidationError(
                    "edge-ref", f"节点 {n.id}({n.type}) 无输出端口 {e.src_port!r}；可用: {list(outs)}"
                )
            if e.dst == n.id and e.dst_port not in ins:
                raise GraphValidationError(
                    "edge-ref", f"节点 {n.id}({n.type}) 无输入端口 {e.dst_port!r}；可用: {list(ins)}"
                )
        # 规则 5: 参数校验（含未知参数名拒绝）
        _validate_params(n, node_cls)

    # 规则 2/3
    seen_dst: dict[tuple[str, str], Edge] = {}
    for e in graph.edges:
        src_cls = registry[graph.nodes[e.src].type]
        dst_cls = registry[graph.nodes[e.dst].type]
        st = src_cls.OUTPUTS.get(e.src_port)
        dt = dst_cls.INPUTS.get(e.dst_port)
        if st != dt:
            raise GraphValidationError(
                "type-match",
                f"边 {e.src}:{e.src_port}({st}) → {e.dst}:{e.dst_port}({dt}) 类型不符；"
                f"模拟→数字必须显式经过 slicer 节点",
            )
        key = (e.dst, e.dst_port)
        if key in seen_dst:
            raise GraphValidationError(
                "single-writer", f"输入端口 {e.dst}:{e.dst_port} 有多条入边"
            )
        seen_dst[key] = e

    # 规则 4: 无环
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in graph.nodes}
    succ: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for e in graph.edges:
        succ[e.src].append(e.dst)

    def dfs(u: str) -> None:
        color[u] = GRAY
        for v in succ[u]:
            if color[v] == GRAY:
                raise GraphValidationError("acyclic", f"图中存在环: {u} → {v}")
            if color[v] == WHITE:
                dfs(v)
        color[u] = BLACK

    for nid in graph.nodes:
        if color[nid] == WHITE:
            dfs(nid)


def _validate_params(spec: NodeSpec, node_cls: type) -> dict[str, Any]:
    """参数规范化：补默认值 + 校验。返回可直接传给 run 的参数字典。"""
    params_decl: Mapping[str, "Param"] = node_cls.PARAMS  # type: ignore[attr-defined]
    unknown = set(spec.params) - set(params_decl)
    if unknown:
        raise GraphValidationError(
            "params", f"节点 {spec.id}({spec.type}) 含未知参数: {sorted(unknown)}；"
            f"可用: {sorted(params_decl)}"
        )
    out: dict[str, Any] = {}
    for name, p in params_decl.items():
        if name in spec.params:
            out[name] = p.coerce(spec.params[name], spec.id)
        elif p.required:
            raise GraphValidationError(
                "params", f"节点 {spec.id}({spec.type}) 缺少必填参数 {name!r}"
            )
        else:
            out[name] = p.default
    return out


@dataclass
class Param:
    """参数声明：default / required / 校验与类型转换。"""

    kind: str = "any"  # any|float|int|bool|str|enum|float_auto|str_list|int_range|float_pos
                       # |hexint|bits|float_list
    default: Any = None
    required: bool = False
    choices: tuple = ()
    lo: float | None = None
    hi: float | None = None
    doc: str = ""

    def coerce(self, value: Any, node_id: str = "?") -> Any:
        try:
            if self.kind == "float":
                v = float(value)
                if self.lo is not None and v < self.lo:
                    raise ValueError(f"{v} < {self.lo}")
                if self.hi is not None and v > self.hi:
                    raise ValueError(f"{v} > {self.hi}")
                return v
            if self.kind == "float_pos":
                v = float(value)
                if v <= 0:
                    raise ValueError("必须为正数")
                return v
            if self.kind == "float_auto":  # 数值或 "auto"
                if isinstance(value, str) and value.lower() == "auto":
                    return "auto"
                return float(value)
            if self.kind == "int":
                v = int(value)
                if self.lo is not None and v < self.lo:
                    raise ValueError(f"{v} < {self.lo}")
                if self.hi is not None and v > self.hi:
                    raise ValueError(f"{v} > {self.hi}")
                return v
            if self.kind == "bool":
                if isinstance(value, str):
                    return value.lower() in ("1", "true", "yes", "on")
                return bool(value)
            if self.kind == "enum":
                s = str(value)
                if s not in self.choices:
                    raise ValueError(f"{s!r} 不在 {list(self.choices)}")
                return s
            if self.kind == "str":
                return str(value)
            if self.kind == "str_list":
                if isinstance(value, str):
                    value = [value]
                return [str(v) for v in value]
            if self.kind == "hexint":  # 十进制或 0x 前缀十六进制
                return int(value, 0) if isinstance(value, str) else int(value)
            if self.kind == "bits":  # "001" 位串（节点内转 tuple）
                b = str(value).strip()
                if not b or any(c not in "01" for c in b):
                    raise ValueError(f"位串应仅含 0/1，得到 {b!r}")
                return b
            if self.kind == "float_list":
                if isinstance(value, str):
                    value = [v for v in value.replace(",", " ").split() if v]
                out = [float(v) for v in value]
                if not out:
                    raise ValueError("需要至少一个数值")
                return out
            return value
        except (TypeError, ValueError) as e:
            raise GraphValidationError(
                "params", f"节点 {node_id} 参数校验失败: {e}（值 {value!r}）"
            ) from e


def evaluate(
    graph: Graph,
    registry: Mapping[str, type],
    targets: list[str],
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    memo: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """拉式记忆化求值：只计算 targets 的祖先；sources 为注入的外部输入。

    memo 可传入外部缓存（如会话按锁保留的跨 run 缓存）：已命中的节点不再重算，
    返回值即该缓存字典（调用方可回存）。跨图复用时由调用方负责淘汰参数变化的
    节点（memo 只按节点 id 键控，不含参数指纹）。
    """
    sources = dict(sources or {})
    memo = memo if memo is not None else {}
    busy: set[str] = set()

    def ev(nid: str) -> dict[str, Any]:
        if nid in memo:
            return memo[nid]
        if nid in busy:
            raise GraphValidationError("acyclic", f"求值中检测到环: {nid}")
        busy.add(nid)
        spec = graph.nodes[nid]
        node_cls = registry[spec.type]
        ins: dict[str, Any] = {}
        if nid in sources:
            ins.update(sources[nid])
        for port, (src, src_port) in graph.incoming(nid).items():
            ins[port] = ev(src)[src_port]
        missing = [p for p in node_cls.INPUTS if p not in ins]
        if missing:
            raise NodeError(
                nid, f"缺少输入端口 {missing}", f"已有: {list(ins)}"
            )
        params = _validate_params(spec, node_cls)
        try:
            memo[nid] = node_cls().run(ins, params)
        except NodeError:
            raise
        except Exception as e:  # 领域内异常统一包装，保留节点上下文
            raise NodeError(nid, f"{type(e).__name__}: {e}", f"输入: {list(ins)}") from e
        busy.discard(nid)
        return memo[nid]

    for t in targets:
        if t not in graph.nodes:
            raise GraphValidationError("edge-ref", f"目标节点不存在: {t}")
        ev(t)
    return memo
