"""I2C 协议绑定（ADR-014）：角色/需求/参数路由/图模板声明。"""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="i2c",
    node_type="i2c_decode",
    roles=("scl", "sda"),
    needs={"min_digital": 2},
    hint="两线同步总线；缺省第 1/2 个数字通道作 SCL/SDA",
    decoder_params=("stretch_warn_s",),
))
