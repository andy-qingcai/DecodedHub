"""公共测试设施：数据路径 + 解码执行便捷函数。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DATA = ROOT / "tests" / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


def run_uart(wave, **params):
    """DigitalWave → UART 解码事件（跳过图，直达节点；图路径由集成测试覆盖）。"""
    from decodehub.decode.graph import Param
    from decodehub.decode.protocols.uart.decode import UartDecodeNode

    decl = UartDecodeNode.PARAMS
    norm = {}
    for k, p in decl.items():
        if k in params:
            norm[k] = p.coerce(params[k])
        else:
            norm[k] = p.default
    if "rx" not in params:
        norm["rx"] = wave.channels[0]
    return UartDecodeNode().run({"in": wave}, norm)["out"]


def run_i2c(wave, **params):
    from decodehub.decode.protocols.i2c.decode import I2cDecodeNode

    decl = I2cDecodeNode.PARAMS
    norm = {}
    for k, p in decl.items():
        if k in params:
            norm[k] = p.coerce(params[k])
        else:
            norm[k] = p.default
    if "scl" not in params:
        norm["scl"] = wave.channels[0]
    if "sda" not in params:
        norm["sda"] = wave.channels[1]
    return I2cDecodeNode().run({"in": wave}, norm)["out"]


def run_spi(wave, **params):
    from decodehub.decode.protocols.spi.decode import SpiDecodeNode

    decl = SpiDecodeNode.PARAMS
    norm = {}
    for k, p in decl.items():
        if k in params:
            norm[k] = p.coerce(params[k])
        else:
            norm[k] = p.default
    if "clk" not in params:
        norm["clk"] = wave.channels[0]
    if "mosi" not in params and len(wave.channels) > 1:
        norm["mosi"] = wave.channels[1]
    if "miso" not in params and len(wave.channels) > 2 and "MISO" in wave.channels:
        norm["miso"] = wave.channels[2]
    if "cs" not in params and "CS" in wave.channels:
        norm["cs"] = wave.channels[wave.channels.index("CS")]
    if not norm.get("mosi") and not norm.get("miso"):
        norm["mosi"] = wave.channels[1]
    return SpiDecodeNode().run({"in": wave}, norm)["out"]


def values_of(events):
    return [e.value for e in events if e.kind == "uart.frame" and not e.errors]


def transfers_of(events):
    return [e for e in events if e.kind == "i2c.transfer"]
