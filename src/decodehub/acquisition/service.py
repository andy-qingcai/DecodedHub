"""IngestService：path (+format, +options) → Capture。嗅探与适配的编排入口。

格式解析唯一经由注册表（ADR-018）：未知/延后格式在 get_spec 报错，
required 选项在解析前由 validate_options 前置强制。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..shared.waves import Capture, make_capture_id
from .adapters import resolve_spec, validate_options
from .sniff import sniff


def load_capture(
    path: str | Path,
    format_key: str | None = None,
    options: dict | None = None,
) -> Capture:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"采集文件不存在: {p}")
    spec = resolve_spec(format_key or sniff(p))
    validate_options(spec, options)
    cap = spec.load(p, options)
    cap.capture_id = make_capture_id(p)
    if cap.meta.captured_at is None:
        cap.meta.captured_at = datetime.fromtimestamp(p.stat().st_mtime)
    return cap


class IngestService:
    """薄封装：保留类形态以便未来扩展（远端源、缓存等）。"""

    def load(self, path, format_key=None, options=None) -> Capture:
        return load_capture(path, format_key, options)
