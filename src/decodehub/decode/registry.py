"""节点注册表：type → Node 类。新增解码器只需 @register（扩展指南见 docs/30）。"""

from __future__ import annotations

from typing import Any, Mapping

NODE_REGISTRY: dict[str, type] = {}


def register(cls):
    """类装饰器：以 cls.TYPE 为键注册节点实现。

    注册期即校验节点契约完整性（docs/30 节点契约：TYPE/INPUTS/OUTPUTS/
    PARAMS/run 缺一不可）——缺失在 @register 时报错，而不是等到建图或
    求值时才以 AttributeError 暴露。
    """
    key = getattr(cls, "TYPE", None)
    if not key or not isinstance(key, str):
        raise ValueError(f"{cls.__name__} 缺少非空 str TYPE 属性")
    if key in NODE_REGISTRY:
        raise ValueError(f"节点类型重复注册: {key}")
    for attr in ("INPUTS", "OUTPUTS", "PARAMS"):
        if not isinstance(getattr(cls, attr, None), Mapping):
            raise ValueError(f"节点 {key}({cls.__name__}) 缺少 {attr} 声明")
    if not callable(getattr(cls, "run", None)):
        raise ValueError(f"节点 {key}({cls.__name__}) 缺少 run(inputs, params)")
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
