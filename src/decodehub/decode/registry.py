"""节点注册表：type → Node 类。新增解码器只需 @register（扩展指南见 docs/30）。"""

from __future__ import annotations

from typing import Any, Mapping

NODE_REGISTRY: dict[str, type] = {}


def register(cls):
    """类装饰器：以 cls.TYPE 为键注册节点实现。"""
    key = getattr(cls, "TYPE", None)
    if not key:
        raise ValueError(f"{cls.__name__} 缺少 TYPE 属性")
    if key in NODE_REGISTRY:
        raise ValueError(f"节点类型重复注册: {key}")
    NODE_REGISTRY[key] = cls
    return cls


def node_catalog() -> list[dict[str, Any]]:
    """供 capabilities / inspect 使用：全部已注册节点的端口与参数签名。"""
    out = []
    for key, cls in sorted(NODE_REGISTRY.items()):
        params = {
            name: {"default": p.default, "doc": p.doc,
                   "choices": list(p.choices) if p.choices else None}
            for name, p in getattr(cls, "PARAMS", {}).items()
        }
        out.append({
            "type": key,
            "inputs": dict(cls.INPUTS),
            "outputs": dict(cls.OUTPUTS),
            "params": params,
        })
    return out


def get_registry() -> Mapping[str, type]:
    return NODE_REGISTRY
