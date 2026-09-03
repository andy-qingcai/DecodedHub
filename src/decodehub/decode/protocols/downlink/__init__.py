"""下行 DBPSK 协议模块（解码节点 + 编码器 + 绑定 + 原理文档）。"""

from .decode import DownlinkDecodeNode  # noqa: F401
from . import binding as _binding  # noqa: F401  (协议绑定注册，ADR-014)
from . import present  # noqa: F401  (呈现注册，ADR-013)
