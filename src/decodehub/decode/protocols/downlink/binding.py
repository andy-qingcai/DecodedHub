"""下行 DBPSK 协议绑定（ADR-014）：上行锚扇入（ADR-011）。

锚点解析（uplink_source 定位）与同触发校验是会话编排——留在应用层；
图模板（克隆上行子图 + apick → downlink_decode(in, sync)）由
build_lock_graph 按 requires_sync 统一构建。
"""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="downlink",
    node_type="downlink_decode",
    roles=("rx",),
    needs={"min_analog": 1, "min_digital": 0},
    hint="下行 DBPSK（以上行帧为锚的槽位包；263kHz 方波载波、延迟线鉴相）。"
         "要求上/下行通道来自同一次采集（同触发）；图上扇入：上行子图 events + 本源 analog",
    analog_direct=True,
    requires_sync="uplink",
    decoder_params=("profile", "fc_nominal", "cycles_per_bit", "n_bits",
                    "slot_offsets_us", "frame_hz", "invert"),
    role_param={"rx": "channel"},
    tool_params_doc={"uplink_source": "上行锚源别名（该源须已锁 uplink；唯一 uplink 锁时可省）"},
))
