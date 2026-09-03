"""IngestService：path (+format, +options) → Capture。嗅探与适配的编排入口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..shared.waves import Capture, make_capture_id
from .adapters import get_adapter
from .sniff import SUPPORTED_FORMATS, sniff


def load_capture(
    path: str | Path,
    format_key: str | None = None,
    options: dict | None = None,
) -> Capture:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"采集文件不存在: {p}")
    key = format_key or sniff(p)
    if key not in SUPPORTED_FORMATS:
        # 嗅探可能返回延后格式的键（如 saleae_data_table）→ 统一走注册表报错
        get_adapter(key)
    adapter = get_adapter(key)
    cap = adapter(p, options)
    cap.capture_id = make_capture_id(p)
    if cap.meta.captured_at is None:
        cap.meta.captured_at = datetime.fromtimestamp(p.stat().st_mtime)
    return cap


class IngestService:
    """薄封装：保留类形态以便未来扩展（远端源、缓存等）。"""

    def load(self, path, format_key=None, options=None) -> Capture:
        return load_capture(path, format_key, options)
