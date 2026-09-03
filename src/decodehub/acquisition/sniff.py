"""格式嗅探：有序规则表，全部失败报 UnknownFormatError(tried)。

规则顺序（docs/40-acquisition.md）：
1. .sal / zip 魔数            → PlannedFormatError（v1 延后，ADR-007）
2. kvdat 魔数（8KB 内）        → kingst_kvdat
3. <SALEAE> 魔数              → PlannedFormatError（v1 延后）
4. .npz 且键含 t_s/v_V        → mho98_npz
5. 文本头嗅探（容忍 BOM）       → mho98_csv / saleae_csv / kingst_csv /
                                 mcu_adc_csv / generic_csv
6. 偶数大小裸二进制            → mcu_adc_bin（需 sample_rate 参数）
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..shared.errors import PlannedFormatError, UnknownFormatError

_KVDAT_MAGIC = b"kvdat\x00\x00\x00"

_TIMEISH = re.compile(r"^(t(ime)?_?(ms|s)?|millis|elapsed|us)$", re.IGNORECASE)
_ADCISH = re.compile(r"^(adc\w*|raw|value|volt\w*|ch\d+|a\d+|v|v_V)$", re.IGNORECASE)
_VOLT_COL = re.compile(r"^(ch\d+|volt\w*|v|v_V)$", re.IGNORECASE)


def _read_head(path: Path, n: int = 8192) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def sniff(path: str | Path) -> str:
    """返回格式键；识别出延后格式抛 PlannedFormatError；失败抛 UnknownFormatError。"""
    p = Path(path)
    tried: list[str] = []
    head = _read_head(p)
    name = p.name.lower()

    # 规则 1: .sal / zip 工程包
    if name.endswith(".sal") or head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(p) as z:
                if "meta.json" in z.namelist():
                    raise PlannedFormatError(
                        str(p), "saleae_sal",
                        "（嗅探到 .sal 工程包；二进制适配器规格已备档，见 ADR-007；请先在 Logic 2 导出 CSV）",
                    )
        except zipfile.BadZipFile:
            pass
        tried.append("sal/zip 工程包")

    # 规则 2: kvdat 魔数（允许前面有 XML 前导）
    if _KVDAT_MAGIC in head:
        return "kingst_kvdat"
    tried.append("kvdat 魔数")

    # 规则 3: Saleae 二进制
    if head[:8] == b"<SALEAE>":
        raise PlannedFormatError(
            str(p), "saleae_binary",
            "（数字/模拟 bin v0/v1 规格已备档；请先在 Logic 2 导出 CSV，见 ADR-007）",
        )
    tried.append("<SALEAE> 魔数")

    # 规则 4: npz
    if name.endswith(".npz") or head[:2] == b"PK":
        try:
            import numpy as np

            with np.load(p, allow_pickle=False) as z:
                if {"t_s", "v_V"} <= set(z.files):
                    return "mho98_npz"
        except Exception:
            pass
        tried.append("npz 键 t_s/v_V")

    # 规则 5: 文本头
    textual = _looks_textual(head)
    if textual:
        key = _sniff_text(_text_lines(head))
        if key:
            return key
    tried.append("文本表头（mho98/saleae/kingst/mcu_adc/generic）")

    # 规则 6: 偶数大小裸二进制 → mcu_adc_bin（采样率校验留给适配器）
    if not textual and p.stat().st_size % 2 == 0 and p.stat().st_size >= 2:
        return "mcu_adc_bin"

    raise UnknownFormatError(str(p), tried)


def _looks_textual(head: bytes) -> bool:
    if not head:
        return False
    sample = head[:2048]
    if sample.count(b"\x00") > 0:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _text_lines(head: bytes) -> list[str]:
    txt = head.decode("utf-8", errors="replace").lstrip("\ufeff")
    return [ln.rstrip("\r") for ln in txt.splitlines()[:8] if ln.strip()]


def _numeric_cells(line: str) -> list[float] | None:
    cells = [c.strip() for c in line.split(",")]
    try:
        return [float(c) for c in cells]
    except ValueError:
        return None


def _sniff_text(lines: list[str]) -> str | None:
    if not lines:
        return None
    first = lines[0]

    if first.startswith("# MHO98 waveform"):
        return "mho98_csv"

    for ln in lines[:3]:
        if ln.startswith("#"):
            continue
        cells_sp = [c.strip() for c in ln.split(", ")]
        cells_comma = [c.strip() for c in ln.split(",")]
        if len(cells_sp) > 1 and cells_sp[0] == "Time[s]":
            return "kingst_csv"
        if len(cells_comma) > 1 and cells_comma[0] == "Time [s]":
            return "saleae_csv"
        if len(cells_comma) > 2 and cells_comma[0] == "name" and "start_time" in ln:
            return "saleae_data_table"

    # 找首个非注释行作为表头
    header = None
    data_lines = []
    for ln in lines:
        if ln.startswith("#"):
            continue
        if header is None:
            header = ln
        else:
            data_lines.append(ln)
    if header is not None:
        cells = [c.strip() for c in header.split(",")]
        if len(cells) >= 2 and _TIMEISH.match(cells[0]):
            return "mcu_adc_csv"
        if len(cells) == 1 and _ADCISH.match(cells[0]):
            return "mcu_adc_csv"
        low = [c.lower() for c in cells]
        if any(c in ("x", "t", "t_s", "time", "time_s") for c in low) and any(
            _VOLT_COL.match(c) for c in low
        ):
            return "generic_csv"
        # 无表头：验证前几行确为 1–2 列纯数值才认领 mcu_adc
        if _numeric_cells(header) and len(header.split(",")) <= 2:
            if all(_numeric_cells(dl) is not None for dl in data_lines[:3]):
                return "mcu_adc_csv"
    return None


SUPPORTED_FORMATS: dict[str, str] = {
    "kingst_csv": "Kingst VIS 数字 CSV（跳变表，表头 Time[s], 通道 0, …）",
    "kingst_bin": "Kingst VIS 裸二进制（u16 LE 位域流；需 options.sample_rate）",
    "kingst_kvdat": "Kingst VIS 工程文件（自描述：率/深度/初始电平）",
    "mho98_csv": "RIGOL MHO98 MCP 导出 CSV（# 前导 + t_s,v_V）",
    "mho98_npz": "RIGOL MHO98 MCP 导出 NPZ（键 t_s/v_V）",
    "mcu_adc_csv": "MCU ADC 串口记录 CSV（time_ms,adc_raw 等变体；单列需 options.sample_rate）",
    "mcu_adc_bin": "MCU ADC 裸二进制（u16 LE；需 options.sample_rate，可选 vref/bits）",
    "saleae_csv": "Saleae Logic 2 数字 CSV（Time [s],Channel 0, …）",
    "generic_csv": "通用模拟 CSV（x/t 列 + 电压列）",
}

PLANNED_FORMATS: dict[str, str] = {
    "saleae_sal": "Saleae .sal 工程包（请先在 Logic 2 导出 CSV）",
    "saleae_binary": "Saleae 二进制导出（请先在 Logic 2 导出 CSV）",
    "saleae_data_table": "Saleae 数据表（是解码结果而非波形；请导出原始数字 CSV）",
}
