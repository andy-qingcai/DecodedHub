"""格式嗅探：按注册表嗅探序遍历各格式的匹配器（ADR-018）。

匹配器本体随 AdapterSpec 登记在 adapters/<fmt>.py（解析与嗅探同源，见
docs/40-acquisition.md 规则 1–6）；本模块只负责：构建共享 SniffCtx（头部字节/
文本性惰性求值）、按 SPECS 插入序遍历、tried 诊断与延后格式报错。
SUPPORTED_FORMATS / PLANNED_FORMATS 为注册表派生，此处 re-export 供
cli / app 使用。
"""

from __future__ import annotations

from pathlib import Path

from ..shared.errors import PlannedFormatError, UnknownFormatError
from .adapters import PLANNED_FORMATS, SPECS, SUPPORTED_FORMATS
from .adapters.spec import SniffCtx

__all__ = ["sniff", "SUPPORTED_FORMATS", "PLANNED_FORMATS"]


def sniff(path: str | Path) -> str:
    """返回格式键；识别出延后格式抛 PlannedFormatError；失败抛 UnknownFormatError(tried)。"""
    p = Path(path)
    ctx = SniffCtx(p)
    tried: list[str] = []
    for spec in SPECS.values():
        if spec.sniff is None:  # 不可嗅探（如 kingst_bin），只能显式 format=
            continue
        if spec.sniff(ctx):
            if spec.load is None:
                raise PlannedFormatError(str(p), spec.key, f"（{spec.planned_note}）")
            return spec.key
        if spec.sniff_hint:
            tried.append(spec.sniff_hint)
    raise UnknownFormatError(str(p), tried)
