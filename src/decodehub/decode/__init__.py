from .events import (
    DecodeReport,
    DecodedEvent,
    I2cEvent,
    SpiEvent,
    UartEvent,
)
from .graph import Edge, Graph, NodeSpec, Param, evaluate, validate
from .registry import NODE_REGISTRY, get_registry, node_catalog, register

# 导入节点/协议包以触发注册（协议解码器在 protocols/，ADR-012）
from . import nodes as _nodes  # noqa: F401,E402
from . import protocols as _protocols  # noqa: F401,E402

__all__ = [
    "DecodeReport", "DecodedEvent", "I2cEvent", "SpiEvent", "UartEvent",
    "Edge", "Graph", "NodeSpec", "Param", "evaluate", "validate",
    "NODE_REGISTRY", "node_catalog", "register",
]
