"""渲染路由注册表：ProtocolLock.graph_kind → 视觉策略（ADR-019）。

叶子渲染器在 plots.py（timing_plot / analog_plot，纯绘图）；本表把
"图形状"（decode 侧绑定给出的 graph_kind 元数据，见 bindings.graph_kind_for）
翻译成"画哪种图、数字波形从哪来、图题/制品怎么标"。应用层 render_timing
只做：查路由 →（按需物化切片）→ route.plot(RenderInput) → 登记制品。

新增一种呈现形态：plots.py 加叶子渲染器 + 在 ROUTES 登记一行（binding 的
graph_kind_for 返回新键）——应用层与工具层零改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..decode.events import DecodedEvent
from ..shared.waves import AnalogChannel, DigitalWave
from .plots import analog_plot, timing_plot


@dataclass(frozen=True)
class RenderInput:
    """一次渲染的全部输入（应用层组装，路由/叶子渲染器只读）。"""

    path: Path
    events: list[DecodedEvent]
    dpi: int
    title: str
    digital: DigitalWave | None = None
    analog: list[AnalogChannel] | None = None
    threshold: float | None = None
    t_min: float | None = None
    t_max: float | None = None
    max_frames: int = 60


def _plot_timing(inp: RenderInput) -> Path:
    return timing_plot(inp.digital, inp.events, inp.path, t_min=inp.t_min, t_max=inp.t_max,
                       max_frames=inp.max_frames, dpi=inp.dpi, title=inp.title)


def _plot_analog_events(inp: RenderInput) -> Path:
    """模拟直达（ADR-010）/锚点扇入：原始波形 + 事件 span（span 走 events 通道）。"""
    return analog_plot(inp.analog, inp.path, digital=None, threshold=inp.threshold,
                       t_min=inp.t_min, t_max=inp.t_max, dpi=inp.dpi, title=inp.title,
                       events=inp.events)


@dataclass(frozen=True)
class RenderRoute:
    """一种图形状的呈现策略。

    label            图题/制品说明用短名（"{protocol} {label} · 源 …"）
    plot             叶子渲染器（吃 RenderInput，产出 PNG 路径）
    needs_slice      True = 数字波形须由应用层从图求值物化（模拟源切片）
    """

    key: str
    label: str
    plot: Callable[[RenderInput], Path]
    needs_slice: bool = False


ROUTES: dict[str, RenderRoute] = {
    "digital": RenderRoute("digital", "解码时序", _plot_timing),
    "sliced": RenderRoute("sliced", "切片时序", _plot_timing, needs_slice=True),
    "analog_direct": RenderRoute("analog_direct", "突发时序", _plot_analog_events),
    "fan_in": RenderRoute("fan_in", "突发时序", _plot_analog_events),
}


def render_route(graph_kind: str) -> RenderRoute:
    """graph_kind → 路由；未知形状（新绑定先行、路由未跟）显式报错。"""
    from ..shared.errors import DecodehubError

    try:
        return ROUTES[graph_kind]
    except KeyError:
        raise DecodehubError(
            f"未知图形状 {graph_kind!r}（render/routes.py 未登记路由）；"
            f"可用: {sorted(ROUTES)}"
        ) from None
