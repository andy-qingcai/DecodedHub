"""上行 DSSS 协议绑定（ADR-014）：模拟直达 + 预条件节点（ADR-010）。"""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="uplink",
    node_type="uplink_decode",
    roles=("rx",),
    needs={"min_analog": 1, "min_digital": 0},
    hint="上行 DSSS（每 60Hz 周期一个 ~248µs 突发帧；PN 相关解扩，"
         "需原始模拟采样——图路径: analog_pick → uplink_precond → uplink_decode）",
    analog_direct=True,
    precond_node_type="uplink_precond",
    decoder_params=("profile", "chip_s", "invert", "unipolar", "msb_first",
                    "pn_word", "pn_len", "pream", "data_bits"),
    precond_params=("profile", "chip_s"),
    role_param={"rx": "channel"},
))
