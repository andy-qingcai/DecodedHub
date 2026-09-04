"""适配器规格（ADR-018）：格式单一登记点的数据结构与共享嗅探启发。

AdapterSpec 把一个采集格式的四件事钉在同一处：
  load        归一化解析 (path, options) -> Capture
  sniff       嗅探匹配器 SniffCtx -> bool；None = 不可嗅探（须显式 format=）
  options     选项声明（name/type/doc/required）→ 派生 MCP options schema 与前置校验
  description 目录一句话（capabilities / 派生清单）

SUPPORTED_FORMATS / PLANNED_FORMATS / capabilities 选项明细 / options schema /
必填前置校验全部由注册表派生——任何一处都不再手工复写第二份清单。
文本表头启发（时间列/ADC 列/电压列正则）为多个 CSV 匹配器共享，一并在此。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...shared.waves import Capture

HEAD_BYTES = 8192


class SniffCtx:
    """一次嗅探的共享上下文：头部字节/文本判定/文本行惰性求值并缓存。

    文本口径（与 docs/40 规则 5 一致）：前 2048 字节可 UTF-8 解码且无 NUL 才算
    文本；文本行取头部前 8 条非空行（容忍 BOM）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name.lower()
        self.size = path.stat().st_size
        self._head: bytes | None = None
        self._textual: bool | None = None
        self._lines: list[str] | None = None

    @property
    def head(self) -> bytes:
        if self._head is None:
            with open(self.path, "rb") as f:
                self._head = f.read(HEAD_BYTES)
        return self._head

    @property
    def textual(self) -> bool:
        if self._textual is None:
            sample = self.head[:2048]
            self._textual = False
            if sample and sample.count(b"\x00") == 0:
                try:
                    sample.decode("utf-8")
                    self._textual = True
                except UnicodeDecodeError:
                    pass
        return self._textual

    @property
    def lines(self) -> list[str]:
        if self._lines is None:
            if not self.textual:
                self._lines = []
            else:
                txt = self.head.decode("utf-8", errors="replace").lstrip("\ufeff")
                self._lines = [ln.rstrip("\r") for ln in txt.splitlines()[:8] if ln.strip()]
        return self._lines

    def noncomment_lines(self) -> list[str]:
        return [ln for ln in self.lines if not ln.startswith("#")]

    def first_header(self) -> tuple[str, list[str]] | None:
        """（首个非注释行, 其后数据行）；无则 None。mcu/generic 表头启发用。"""
        header: str | None = None
        data: list[str] = []
        for ln in self.lines:
            if ln.startswith("#"):
                continue
            if header is None:
                header = ln
            else:
                data.append(ln)
        return None if header is None else (header, data)


# ---- 共享文本表头启发（docs/40-acquisition.md 嗅探规则 5） -------------------

TIMEISH = re.compile(r"^(t(ime)?_?(ms|s)?|millis|elapsed|us)$", re.IGNORECASE)
ADCISH = re.compile(r"^(adc\w*|raw|value|volt\w*|ch\d+|a\d+|v|v_V)$", re.IGNORECASE)
VOLT_COL = re.compile(r"^(ch\d+|volt\w*|v|v_V)$", re.IGNORECASE)


def numeric_cells(line: str) -> list[float] | None:
    cells = [c.strip() for c in line.split(",")]
    try:
        return [float(c) for c in cells]
    except ValueError:
        return None


SniffFn = Callable[[SniffCtx], bool]
LoadFn = Callable[[Path, dict | None], Capture]


@dataclass(frozen=True)
class OptionField:
    """格式选项声明。required=True 由注册表在解析前强制（缺失即 IngestError）。"""

    name: str
    type: str = "string"  # JSON-schema 词表: string | number | integer | boolean
    doc: str = ""
    required: bool = False


@dataclass(frozen=True)
class AdapterSpec:
    """一个采集格式的完整登记（解析 + 嗅探 + 选项 + 目录描述）。

    load=None 表示延后支持（planned）：嗅探命中抛 PlannedFormatError，
    显式 format= 走注册表报错；此时 planned_note 必填。
    """

    key: str
    description: str
    load: LoadFn | None = None
    sniff: SniffFn | None = None
    sniff_hint: str = ""  # 未命中时进入 UnknownFormatError 的 tried 诊断列表
    options: tuple[OptionField, ...] = ()
    planned_note: str = ""
