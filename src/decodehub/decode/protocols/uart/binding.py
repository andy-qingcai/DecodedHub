"""UART 协议绑定（ADR-014）：角色/需求/参数路由/图模板声明。"""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="uart",
    node_type="uart_decode",
    roles=("rx",),
    needs={"min_digital": 1},
    hint="单线异步串口；rx 角色缺省取第一个数字通道",
    decoder_params=("baud", "data_bits", "parity", "stop_bits", "invert", "bit_order"),
))
