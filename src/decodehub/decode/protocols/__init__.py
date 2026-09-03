"""协议模块注册表（ADR-012）：一协议一目录（decode.py + encode.py + README.md）。

导入本包即触发全部协议解码器的注册（@register）。
新增协议 = 新目录三件套 + 此处一行导入（见 docs/30 扩展指南）。
"""

from . import uart, i2c, spi, uplink, downlink  # noqa: F401,E402
