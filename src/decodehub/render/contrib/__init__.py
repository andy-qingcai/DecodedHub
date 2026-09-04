"""协议客制渲染注册表（ADR-022）：(protocol, graph_kind) → RenderRoute。

通用路由（routes.py，graph_kind 维度）是全部协议的缺省画法；协议要"自身最佳
显示"时在本包放 <protocol>.py 自注册客制路由。依赖方向与 ADR-013/019 一致：
本包（呈现上下文）可 import decode 的事件/波形，decode 侧不感知绘图——协议包
不得反向注册，故注册点住 render 侧，经 pkgutil 发现 contrib 模块（新增文件
零登记即生效）。分派优先级：客制 > 通用 > 显式报错；注册与顺序无关，重复
(import 期抛 ValueError) 是并行开发的 fail-fast 防线。
"""

from __future__ import annotations

from ..routes import RenderRoute, render_route

_CONTRIB: dict[tuple[str, str], RenderRoute] = {}


def register_contrib_route(protocol: str, graph_kind: str, route: RenderRoute) -> None:
    """登记一个协议客制路由；重复 (protocol, graph_kind) 抛 ValueError。"""
    key = (protocol, graph_kind)
    if key in _CONTRIB:
        raise ValueError(f"协议客制渲染路由重复注册: {key}")
    _CONTRIB[key] = route


def contrib_route(protocol: str, graph_kind: str) -> RenderRoute | None:
    """(protocol, graph_kind) → 客制路由；未登记返回 None。"""
    return _CONTRIB.get((protocol, graph_kind))


def all_contrib_routes() -> dict[tuple[str, str], RenderRoute]:
    """全部已登记客制路由（键序 = 注册序，仅供诊断/测试）。"""
    return dict(_CONTRIB)


def resolve_route(protocol: str, graph_kind: str) -> RenderRoute:
    """两级分派：协议客制优先，graph_kind 通用路由兜底（未登记显式报错）。"""
    return _CONTRIB.get((protocol, graph_kind)) or render_route(graph_kind)


def load_contrib_modules() -> None:
    """pkgutil 发现并导入本包全部模块——新增 <protocol>.py 无需任何登记行。"""
    import importlib
    import pkgutil

    for mod in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{mod.name}")


load_contrib_modules()
