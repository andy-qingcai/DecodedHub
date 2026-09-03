"""通用（非协议）图节点：通道挑选 / 阈值切片 / 事件过滤。

协议解码器在 decodehub.decode.protocols/<协议>/（ADR-012）。
"""

from . import picks, slicer, filters  # noqa: F401,E402
