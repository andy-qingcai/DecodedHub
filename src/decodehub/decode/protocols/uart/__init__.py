"""UART 协议模块（解码节点 + 编码器 + 原理文档 README.md）。"""

from .decode import UartDecodeNode  # noqa: F401
from . import present  # noqa: F401  (呈现注册，ADR-013)
