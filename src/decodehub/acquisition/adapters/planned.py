"""延后支持格式的规格（v1 裁决见 ADR-007；ADR-018 起与可解析格式同表登记）。

load=None：嗅探命中即 PlannedFormatError；显式 format= 走注册表报错（同一 note）。
"""

from __future__ import annotations

import zipfile

from .spec import AdapterSpec, SniffCtx


def _sniff_sal(ctx: SniffCtx) -> bool:
    if not (ctx.name.endswith(".sal") or ctx.head[:4] == b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(ctx.path) as z:
            return "meta.json" in z.namelist()
    except zipfile.BadZipFile:
        return False


SALEAE_SAL = AdapterSpec(
    key="saleae_sal",
    description="Saleae .sal 工程包（请先在 Logic 2 导出 CSV）",
    planned_note=".sal 工程包（zip）二进制规格已备档于 ADR-007——请在 Logic 2 导出数字 CSV 后重试",
    sniff=_sniff_sal,
    sniff_hint="sal/zip 工程包",
)


def _sniff_saleae_binary(ctx: SniffCtx) -> bool:
    return ctx.head[:8] == b"<SALEAE>"


SALEAE_BINARY = AdapterSpec(
    key="saleae_binary",
    description="Saleae 二进制导出（请先在 Logic 2 导出 CSV）",
    planned_note="数字/模拟二进制 v0/v1 规格已备档于 ADR-007——请在 Logic 2 导出 CSV 后重试",
    sniff=_sniff_saleae_binary,
    sniff_hint="<SALEAE> 魔数",
)


def _sniff_data_table(ctx: SniffCtx) -> bool:
    for ln in ctx.lines[:3]:
        if ln.startswith("#"):
            continue
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) > 2 and cells[0] == "name" and "start_time" in ln:
            return True
    return False


SALEAE_DATA_TABLE = AdapterSpec(
    key="saleae_data_table",
    description="Saleae 数据表（是解码结果而非波形；请导出原始数字 CSV）",
    planned_note="数据表是解码结果而非原始波形——请导出原始数字 CSV",
    sniff=_sniff_data_table,
    sniff_hint="文本头（name,…,start_time）",
)

SPECS = (SALEAE_SAL, SALEAE_BINARY, SALEAE_DATA_TABLE)
