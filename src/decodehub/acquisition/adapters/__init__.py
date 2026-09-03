"""适配器注册表：格式键 → load(path, options) -> Capture。"""

from ...shared.errors import DecodehubError
from .generic_csv import load as load_generic_csv
from .kingst_bin import load as load_kingst_bin
from .kingst_csv import load as load_kingst_csv
from .kingst_kvdat import load as load_kingst_kvdat
from .mcu_adc_bin import load as load_mcu_adc_bin
from .mcu_adc_csv import load as load_mcu_adc_csv
from .mho98_csv import load as load_mho98_csv
from .mho98_npz import load as load_mho98_npz
from .saleae_csv import load as load_saleae_csv

ADAPTERS = {
    "kingst_csv": load_kingst_csv,
    "kingst_bin": load_kingst_bin,
    "kingst_kvdat": load_kingst_kvdat,
    "mho98_csv": load_mho98_csv,
    "mho98_npz": load_mho98_npz,
    "mcu_adc_csv": load_mcu_adc_csv,
    "mcu_adc_bin": load_mcu_adc_bin,
    "saleae_csv": load_saleae_csv,
    "generic_csv": load_generic_csv,
}

PLANNED = {
    "saleae_sal", "saleae_binary", "saleae_data_table",
}

_PLANNED_NOTES = {
    "saleae_sal": "请在 Logic 2 中导出 CSV（digital → CSV）后重试",
    "saleae_binary": "请在 Logic 2 中导出 CSV（digital → CSV）后重试",
    "saleae_data_table": "数据表是解码结果而非原始波形；请导出原始数字 CSV",
}


def get_adapter(format_key: str):
    if format_key in PLANNED:
        raise DecodehubError(
            f"格式 {format_key} 在当前版本延后支持：{_PLANNED_NOTES[format_key]}（ADR-007）"
        )
    try:
        return ADAPTERS[format_key]
    except KeyError:
        raise DecodehubError(f"未知格式键 {format_key!r}；可用: {sorted(ADAPTERS)}") from None
