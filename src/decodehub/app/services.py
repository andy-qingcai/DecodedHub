"""应用层用例编排（docs/30 运行时数据流）。

多源模型（ADR-008 v1.2）：**每源独立协议锁与解码**——各源时间轴独立、互不影响；
合并/对齐是库能力（Project.merged），不进入工具流程。
协议目录 PROTOCOL_CATALOG 是"新协议接入"的唯一登记点（扩展指南）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from ..acquisition.project import Project, SourceEntry
from ..acquisition.service import load_capture
from ..acquisition.sniff import PLANNED_FORMATS, SUPPORTED_FORMATS
from ..decode.events import DecodeReport, DecodedEvent
from ..decode.graph import Graph, evaluate, validate
from ..decode.presentation import all_preview_kinds
from ..decode.registry import get_registry
from ..render.artifacts import ArtifactStore
from ..render.format import events_markdown, report_csv_rows, report_json
from ..render.plots import analog_plot, timing_plot
from ..shared.errors import ProtocolLockError
from ..shared.waves import Capture
from .session import ProtocolLock, SessionState, Stage

# ------------------------------------------------------------ 协议目录 ---

PROTOCOL_CATALOG: dict[str, dict] = {
    "uart": {
        "roles": ["rx"],
        "params": {
            "baud": "波特率数值或 'auto'（默认 auto）",
            "data_bits": "5–9（默认 8）",
            "parity": "N/O/E（默认 N）",
            "stop_bits": "1/1.5/2（默认 1）",
            "invert": "线路反相（默认 false）",
            "bit_order": "lsb/msb（默认 lsb）",
            "rx": "显式指定 RX 通道名（覆盖自动映射）",
        },
        "needs": {"min_digital": 1},
        "hint": "单线异步串口；rx 角色缺省取第一个数字通道",
    },
    "i2c": {
        "roles": ["scl", "sda"],
        "params": {
            "scl": "SCL 通道名（覆盖自动映射）",
            "sda": "SDA 通道名（覆盖自动映射）",
            "stretch_warn_s": "时钟拉伸告警阈值秒（默认 0.001）",
        },
        "needs": {"min_digital": 2},
        "hint": "两线同步总线；缺省第 1/2 个数字通道作 SCL/SDA",
    },
    "spi": {
        "roles": ["clk", "mosi", "miso", "cs"],
        "params": {
            "clk": "CLK 通道名", "mosi": "MOSI 通道名", "miso": "MISO 通道名",
            "cs": "CS 通道名（可省略：按位计数分词）",
            "cpol": "0/1（默认 0）", "cpha": "0/1（默认 0）",
            "word_bits": "1–32（默认 8）", "bit_order": "msb/lsb（默认 msb）",
            "cs_active": "low/high（默认 low）",
        },
        "needs": {"min_digital": 2},
        "hint": "四线同步总线（MISO 可省）；缺省第 1/2/3/4 个数字通道作 CLK/MOSI/MISO/CS",
    },
    "downlink": {
        "roles": ["rx"],
        "params": {
            "rx": "下行模拟通道名（覆盖自动映射）",
            "uplink_source": "上行锚源别名（该源须已锁 uplink；唯一 uplink 锁时可省）",
            "profile": "下行协议档案名（默认 default；档案只是参数预设）",
            "fc_nominal": "标称载波 Hz（默认 263000）",
            "cycles_per_bit": "每 bit 载波周期数（默认 10）",
            "n_bits": "包符号数含起始位（默认 17 = 1 起始 + 16 数据）",
            "slot_offsets_us": "槽位偏移 µs 列表（默认 1970,4748,7525,10303,13081,15858）；"
                              "锚点偏移 delta 由接收机逐包自校准，不配置",
            "frame_hz": "上行帧网格频率 Hz（默认 60）",
            "invert": "差分极性反转（默认 false）",
        },
        "needs": {"min_analog": 1, "min_digital": 0},
        "hint": "下行 DBPSK（以上行帧为锚的槽位包；263kHz 方波载波、延迟线鉴相）。"
                "要求上/下行通道来自同一次采集（同触发）；图上扇入：上行子图 events + 本源 analog",
    },
    "uplink": {
        "roles": ["rx"],
        "params": {
            "rx": "模拟通道名（覆盖自动映射；模拟信号直达解码器，不经阈值切片）",
            "profile": "协议档案名（默认 default；档案只是参数预设，非协议常量）",
            "chip_s": "标称码片周期秒（缺省 1e-6；接收机自动估计实际速率）",
            "pn_word": "PN 扩频字（默认 0x3DA60E45，接受 0x 前缀）",
            "pn_len": "PN 码片数（默认 31）",
            "pream": "前导位串（默认 '001'）",
            "data_bits": "每帧数据位数（默认 5）",
            "invert": "物理极性反相（默认 false）",
            "unipolar": "码片 0/+A 编码（默认双极性 -A/+A）",
            "msb_first": "PN 字高位在先（默认 true）",
        },
        "needs": {"min_analog": 1, "min_digital": 0},
        "hint": "上行 DSSS（每 60Hz 周期一个 ~248µs 突发帧；PN 相关解扩，"
                "需原始模拟采样——图路径: analog_pick → uplink_precond → uplink_decode）",
    },
}

_ROLE_ALIASES = {
    "rx": {"rx", "rxd", "din", "di", "sdin"},
    "tx": {"tx", "txd", "dout", "do", "sdout"},
    "scl": {"scl", "sck", "clk", "clock", "a5"},
    "sda": {"sda", "dio", "data", "a4"},
    "clk": {"sck", "clk", "scl", "clock"},
    "mosi": {"mosi", "dout", "do", "sdout", "copi"},
    "miso": {"miso", "din", "di", "sdin", "cipo"},
    "cs": {"cs", "nss", "ss", "nsc", "enable", "ce"},
}

_CH_NUM = re.compile(r"(?:通道|channel|ch|d)\s*(\d+)", re.IGNORECASE)


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).lower()


def _role_hit(name: str, aliases: set[str]) -> bool:
    if _norm(name) in aliases:
        return True
    if ":" in name:
        return _norm(name.rsplit(":", 1)[-1]) in aliases
    return False


def auto_map_channels(chs: list[str], protocol: str, overrides: dict) -> dict:
    """角色 → 通道名：先按常见名匹配，再按序号回退，最后显式覆盖。"""
    if not chs:
        raise ProtocolLockError(f"协议 {protocol} 需要通道，但该源没有任何通道（数字或模拟）")
    numbers = {}
    for c in chs:
        m = _CH_NUM.search(_norm(c))
        numbers[c] = int(m.group(1)) if m else None

    roles = PROTOCOL_CATALOG[protocol]["roles"]
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for role in roles:
        aliases = _ROLE_ALIASES.get(role, {role})
        hit = None
        for c in chs:
            if c in used:
                continue
            if _role_hit(c, aliases):
                hit = c
                break
        if hit is None:  # 按序号（数字小的在前）
            numbered = [c for c in chs if numbers[c] is not None and c not in used]
            others = [c for c in chs if numbers[c] is None and c not in used]
            pool = sorted(numbered, key=lambda c: numbers[c]) + others
            if pool:
                hit = pool[0]
        if hit is not None:
            mapping[role] = hit
            used.add(hit)

    for role in roles:
        if overrides.get(role):
            want = overrides[role]
            if want not in chs:
                raise ProtocolLockError(f"通道 {want!r} 不存在；可用: {chs}")
            mapping[role] = want

    need_min = PROTOCOL_CATALOG[protocol]["needs"].get("min_digital", 0)
    required_roles = [r for r in roles if r not in ("miso", "cs")]
    got = [r for r in required_roles if r in mapping]
    if len(got) < len(required_roles) or len(mapping) < need_min:
        raise ProtocolLockError(
            f"协议 {protocol} 至少需要 {need_min} 个数字通道（角色 {roles}），"
            f"实际可用 {len(chs)} 个: {chs}"
        )
    return mapping


# ---------------------------------------------------------------- 源管理 ---

def _default_alias(project: Project | None, path: str) -> str:
    stem = Path(path).stem
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]+", "_", stem)[:24] or "src"
    taken = {e.alias for e in project.entries} if project else set()
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _parse_t_wall(options: dict | None) -> datetime | None:
    raw = (options or {}).get("t_wall")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw))


def _add_entry(state: SessionState, path: str, fmt: str | None, options: dict | None) -> tuple[str, Capture]:
    cap = load_capture(path, format_key=fmt, options=options)
    opts = dict(options or {})
    alias = opts.pop("alias", None) or _default_alias(state.project, path)
    if state.project is None:
        state.project = Project()
        state.stage = Stage.SOURCE_LOCKED
        state.locks.clear()
        state.reports.clear()
    state.project.add(SourceEntry(alias=alias, capture=cap,
                                  offset=float(opts.get("offset", 0.0)),
                                  t_wall=_parse_t_wall(opts),
                                  options=opts))
    return alias, cap


def ingest(state: SessionState, path: str, fmt: str | None, options: dict | None) -> str:
    """摄取采集：DISCOVERY 下创建工程；SOURCE_LOCKED 之后等价于 add_source。"""
    if state.project is not None and state.project.entries:
        return add_source(state, path, fmt, options)
    alias, cap = _add_entry(state, path, fmt, options)
    names = cap.channel_names()
    rate = cap.meta.sample_rate
    lines = [
        f"✅ 已摄取采集 `{cap.capture_id}`（源别名 `{alias}`）",
        f"- 格式: {cap.meta.format_key}（源: {cap.meta.source_kind}）",
        f"- 时长: {cap.duration:.6g} s；采样率: "
        + (f"{rate:.6g} Hz" if rate else "文件未提供（不影响解码）"),
    ]
    if names["digital"]:
        lines.append(f"- 数字通道 {len(names['digital'])}: {names['digital']}")
    if names["analog"]:
        lines.append(f"- 模拟通道 {len(names['analog'])}: {names['analog']}")
    lines.append("\n下一步: `add_source`（多采集器并行分析）/ `describe_capture` / `lock_protocol`")
    return "\n".join(lines)


def add_source(state: SessionState, path: str, fmt: str | None, options: dict | None) -> str:
    """追加源（各源独立分析，不影响已锁定的协议；ADR-008 v1.2）。"""
    if state.project is None:
        raise RuntimeError("先 lock_source 创建工程")
    alias, cap = _add_entry(state, path, fmt, options)
    names = cap.channel_names()
    lines = [
        f"✅ 已追加源 `{alias}`（{cap.meta.format_key}），工程共 {len(state.project.entries)} 个源: "
        + ", ".join(f"`{e.alias}`" for e in state.project.entries),
        f"- 新源: 数字 {len(names['digital'])} / 模拟 {len(names['analog'])} 通道",
        f"- 已锁协议的源: {sorted(state.locks) or '（无）'}（各源独立解码，互不影响）",
        f"\n下一步: `lock_protocol(protocol=…, source=\"{alias}\")`（多源时需指定 source）",
    ]
    return "\n".join(lines)


def describe_capture(state: SessionState) -> str:
    if state.project is None or not state.project.entries:
        raise RuntimeError("会话无采集源")
    lines = []
    for e in state.project.entries:
        cap = e.capture
        names = cap.channel_names()
        lines.append(
            f"### 源 `{e.alias}` [{cap.meta.format_key}] · 时长 {cap.duration:.6g}s"
            + (f" · 已锁 " + ",".join(
                l.protocol for l in state.locks.values() if l.source == e.alias)
               if any(l.source == e.alias for l in state.locks.values()) else "")
        )
        if cap.digital is not None:
            for name in cap.digital.channels:
                et, _lv = cap.digital.edge_stream(name)
                if et.size >= 2:
                    pulse = float(np.min(np.diff(et)))
                    freq = f"~{1.0 / (2 * pulse):.4g} Hz" if pulse > 0 else "-"
                else:
                    freq = "-"
                act = et.size / max(cap.digital.duration, 1e-12)
                lines.append(f"- 数字 `{name}`: 跳变 {et.size}（活动率 {act:.3g}/s，最短脉冲 {freq}）")
        for ch in cap.analog:
            v = ch.samples
            lines.append(
                f"- 模拟 `{ch.name}`: {ch.n} 点 @ "
                f"{f'{1 / ch.dt:.6g}' if ch.dt else '非均匀'}Hz，{ch.units}，"
                f"Vpp={float(np.ptp(v)):.4g}，min={float(v.min()):.4g}，max={float(v.max()):.4g}"
            )
        lines.append("")
    lines.append("下一步: `lock_protocol(protocol=…)`（多源时 params/source 指定源）")
    return "\n".join(lines)


# ------------------------------------------------------------ 协议锁定 ---

def lock_protocol(state: SessionState, protocol: str, params: dict | None,
                  source: str | None) -> tuple[str, Graph]:
    """按源锁定协议：source 缺省 = 唯一源；多源必须显式指定。"""
    if state.project is None or not state.project.entries:
        raise ProtocolLockError("请先 lock_source")
    if protocol not in PROTOCOL_CATALOG:
        raise ProtocolLockError(f"未知协议 {protocol!r}；可用: {list(PROTOCOL_CATALOG)}")
    if source is None and len(state.project.entries) > 1:
        raise ProtocolLockError(
            f"多源工程必须指定 source；可用: {state.source_aliases()}"
        )
    cap = state.capture_of(source)
    alias = source or state.single_alias()
    params = dict(params or {})

    if protocol == "uplink":
        chs = [c.name for c in cap.analog]  # DSSS 需要模拟通道（角色映射基于模拟名）
    else:
        chs = list(cap.digital.channels) if cap.digital is not None else [c.name for c in cap.analog]
    cmap = auto_map_channels(chs, protocol, params)

    graph = Graph()
    decoder_params: dict = {}
    if protocol == "uart":
        decoder_params = {k: v for k, v in params.items()
                          if k in ("baud", "data_bits", "parity", "stop_bits", "invert", "bit_order")}
        decoder_params["rx"] = cmap["rx"]
    elif protocol == "i2c":
        decoder_params = {k: v for k, v in params.items() if k == "stretch_warn_s"}
        decoder_params.update(scl=cmap["scl"], sda=cmap["sda"])
    elif protocol == "spi":
        decoder_params = {k: v for k, v in params.items()
                          if k in ("cpol", "cpha", "word_bits", "bit_order", "cs_active")}
        decoder_params["clk"] = cmap["clk"]
        if "mosi" in cmap:
            decoder_params["mosi"] = cmap["mosi"]
        if "miso" in cmap:
            decoder_params["miso"] = cmap["miso"]
        if "cs" in cmap:
            decoder_params["cs"] = cmap["cs"]
    elif protocol == "uplink":
        decoder_params = {k: v for k, v in params.items()
                          if k in ("profile", "chip_s", "invert", "unipolar", "msb_first",
                                   "pn_word", "pn_len", "pream", "data_bits")}
        decoder_params["channel"] = cmap["rx"]

    source_inputs: dict[str, str] = {}
    if protocol == "downlink":
        if cap.digital is not None or not cap.analog:
            raise ProtocolLockError(
                "downlink 需要模拟通道（DBPSK 解调需要原始波形）；该源请用示波器导出"
            )
        # 定位上行锚（ADR-011：下行以上行帧网格为锚，扇入 + 跨源注入）
        ul_alias = params.get("uplink_source")
        uplink_locks = {l.source: l for l in state.locks.values() if l.protocol == "uplink"}
        if ul_alias:
            if ul_alias not in uplink_locks:
                raise ProtocolLockError(
                    f"uplink_source={ul_alias!r} 未锁定 uplink 协议；"
                    f"已锁 uplink 的源: {sorted(uplink_locks) or '（无）'}"
                )
        elif len(uplink_locks) == 1:
            ul_alias = next(iter(uplink_locks))
        else:
            raise ProtocolLockError(
                "downlink 需要 uplink_source 参数指定上行锚源"
                + (f"；已锁 uplink 的源: {sorted(uplink_locks)}" if uplink_locks
                   else "（请先对该源 lock_protocol(protocol='uplink')）")
            )
        params["uplink_source"] = ul_alias  # 物化解析结果（重建/档案复用）
        ul_lock = uplink_locks[ul_alias]
        ul_cap = state.capture_of(ul_alias)
        if not ul_cap.analog:
            raise ProtocolLockError(f"上行锚源 {ul_alias!r} 无模拟通道")
        # 同触发校验：上行/下行必须来自同一次采集（时间轴一致），跨仪器无法对齐
        t0_ul = float(ul_cap.analog[0].t0)
        t0_dl = float(cap.analog[0].t0)
        if abs(t0_ul - t0_dl) > 1e-3:
            raise ProtocolLockError(
                f"上行({ul_alias}) 与下行源 t0 相差 {abs(t0_ul - t0_dl)*1e3:.2f} ms——"
                f"下行锚定要求两通道来自同一次采集（同触发）。请用示波器双通道同时导出。"
            )
        # 克隆上行子图（加前缀），接扇入
        idmap = {}
        for nid, spec in ul_lock.graph.nodes.items():
            idmap[nid] = f"ul_{nid}"
            graph.add_node(idmap[nid], spec.type, **spec.params)
        for e in ul_lock.graph.edges:
            graph.add_edge(idmap[e.src], e.src_port, idmap[e.dst], e.dst_port)
        dl_params = {k: v for k, v in params.items()
                     if k in ("profile", "fc_nominal", "cycles_per_bit", "n_bits",
                              "slot_offsets_us", "frame_hz", "invert")}
        dl_params["channel"] = cmap["rx"]
        graph.add_node("apick", "analog_pick")
        graph.add_node("downlink_decode", "downlink_decode", **dl_params)
        graph.add_edge("apick", "out", "downlink_decode", "in")
        graph.add_edge(idmap["uplink_decode"], "out", "downlink_decode", "sync")
        source_inputs = {idmap["apick"]: ul_alias, "apick": alias}
    elif protocol == "uplink":
        if cap.digital is not None or not cap.analog:
            raise ProtocolLockError(
                "uplink 需要模拟通道（DSSS 解扩需要幅度信息，数字切片信号不可用）；"
                "该源请改用示波器/MCU ADC 导出"
            )
        # ADR-010：模拟直达路径——analog_pick → uplink_precond → uplink_decode
        # （不经 slicer：一位切片毁掉 PN 相关所需的软信息）
        precond_params = {k: v for k, v in decoder_params.items() if k in ("profile", "chip_s")}
        precond_params["channel"] = cmap["rx"]
        graph.add_node("apick", "analog_pick")
        graph.add_node("upre", "uplink_precond", **precond_params)
        graph.add_edge("apick", "out", "upre", "in")
        graph.add_edge("upre", "out", "uplink_decode", "in")
    elif cap.digital is not None:
        graph.add_node("pick", "digital_pick")
        graph.add_edge("pick", "out", f"{protocol}_decode", "in")
    else:
        # 模拟源：analog_pick → slicer → 解码器（ADR-002：显式切片节点）
        slicer_params: dict = {}
        if "threshold" in params:
            slicer_params["threshold"] = params["threshold"]
        if "hysteresis" in params:
            slicer_params["hysteresis"] = params["hysteresis"]
        graph.add_node("apick", "analog_pick")
        graph.add_node("slice", "slicer", **slicer_params)
        graph.add_edge("apick", "out", "slice", "in")
        graph.add_edge("slice", "out", f"{protocol}_decode", "in")
    if f"{protocol}_decode" not in graph.nodes:
        graph.add_node(f"{protocol}_decode", f"{protocol}_decode", **decoder_params)
    validate(graph, get_registry())
    if not source_inputs:
        source_inputs = {"apick" if "apick" in graph.nodes else "pick": alias}

    lock_key = f"{alias}|{protocol}"
    state.locks[lock_key] = ProtocolLock(source=alias, protocol=protocol,
                                         params=params, channel_map=cmap, graph=graph,
                                         source_inputs=source_inputs)
    state.stage = Stage.READY
    role_txt = ", ".join(f"{r}→`{c}`" for r, c in cmap.items())
    plan = (
        f"✅ 源 `{alias}` 协议已锁定: **{protocol}**（通道映射: {role_txt}）\n\n"
        f"解码计划（inspect_graph 可查）:\n```\n{graph.to_text()}\n```"
    )
    return plan, graph


def unlock_protocol(state: SessionState, source: str | None, protocol: str | None) -> str:
    if not state.locks:
        return "当前没有已锁定的协议。"
    hits = [(k, l) for k, l in state.locks.items()
            if source is None or k == source or l.source == source]
    if protocol:
        hits = [(k, l) for k, l in hits if l.protocol == protocol]
    if not hits:
        return f"无匹配协议锁（source={source!r}, protocol={protocol!r}）；" \
               f"已有: {sorted(state.locks)}"
    for k, _l in hits:
        state.locks.pop(k, None)
        state.reports.pop(k, None)
    if not state.locks:
        state.stage = Stage.SOURCE_LOCKED
    left = [f"{l.source}|{l.protocol}" for l in state.locks.values()]
    return "已解锁 " + ", ".join(k for k, _ in hits) + f"；剩余: {left or '无'}"


# ---------------------------------------------------------------- 解码 ---

def _locks_for(state: SessionState, source: str | None) -> list[tuple[str, ProtocolLock]]:
    """源别名（或协议锁键）→ [(键, 锁)]。"""
    if not state.locks:
        raise ProtocolLockError("请先 lock_protocol")
    if source is None:
        return list(state.locks.items())
    if source in state.locks:  # 完整锁键
        return [(source, state.locks[source])]
    hits = [(k, l) for k, l in state.locks.items() if l.source == source]
    if not hits:
        raise ProtocolLockError(
            f"源 {source!r} 尚未锁定协议；已锁: "
            f"{[f'{l.source}|{l.protocol}' for l in state.locks.values()]}"
        )
    return hits


def run_decode(state: SessionState, overrides: dict | None, source: str | None) -> str:
    """执行解码：source/键 缺省 = 全部锁（一源可有多协议锁并行）；overrides 单锁时有效。"""
    targets = _locks_for(state, source)
    if overrides and len(targets) > 1:
        raise ProtocolLockError("overrides 仅在指定单锁（source 或 源|协议 键）时有效")

    sections = []
    for key, lock in targets:
        graph, params = lock.graph, lock.params
        if overrides:
            merged = {**lock.params, **overrides}
            if lock.protocol == "downlink":
                graph, params = _rebuild_downlink(lock, merged), merged
            else:
                _p, graph = lock_protocol(state, lock.protocol, merged, lock.source)
                params = merged
        node_id = f"{lock.protocol}_decode"
        sources = {node: {"in": state.capture_of(a)}
                   for node, a in lock.source_inputs.items()}
        t0 = time.perf_counter()
        memo = evaluate(graph, get_registry(), targets=[node_id], sources=sources)
        wall_ms = (time.perf_counter() - t0) * 1000
        events: list[DecodedEvent] = memo[node_id]["out"]
        report = DecodeReport(
            protocol=lock.protocol,
            params={k: v for k, v in graph.nodes[node_id].params.items()},
            events=events, node_id=node_id, wall_ms=wall_ms,
        )
        state.reports[key] = report
        sections.append(_summary_section(lock.source, report))
    n = len(targets)
    header = "## 解码完成\n" + (f"（{n} 个协议锁并行解码）\n" if n > 1 else "")
    return header + "\n".join(sections)


def _rebuild_downlink(lock: ProtocolLock, merged_params: dict) -> Graph:
    """下行锁参数重建：复用锁内嵌的上行子图（同别名上行锁可能已不存在）。"""
    graph = Graph()
    for nid, spec in lock.graph.nodes.items():
        if nid.startswith("ul_"):
            graph.add_node(nid, spec.type, **spec.params)
    for e in lock.graph.edges:
        if e.src.startswith("ul_") and e.dst.startswith("ul_"):
            graph.add_edge(e.src, e.src_port, e.dst, e.dst_port)
    dl = {k: v for k, v in merged_params.items()
          if k in ("profile", "fc_nominal", "cycles_per_bit", "n_bits",
                   "slot_offsets_us", "frame_hz", "invert")}
    dl["channel"] = lock.graph.nodes["downlink_decode"].params.get("channel", "")
    graph.add_node("apick", "analog_pick")
    graph.add_node("downlink_decode", "downlink_decode", **dl)
    graph.add_edge("apick", "out", "downlink_decode", "in")
    graph.add_edge("ul_uplink_decode", "out", "downlink_decode", "sync")
    validate(graph, get_registry())
    return graph


def _summary_section(alias: str, report: DecodeReport) -> str:
    c = report.counts()
    by_kind = "\n".join(f"  - {k}: {v}" for k, v in c["by_kind"].items()) or "  - 无"
    err_line = (f"  ⚠️ 解码错误事件 {c['errors']} 个（get_events has_errors=true）"
                if c["errors"] else "")
    preview = [e for e in report.events if e.kind in all_preview_kinds()][:20]
    table = events_markdown(preview)
    return (f"### 源 `{alias}`（{report.protocol}，{report.wall_ms:.1f} ms）\n"
            f"事件 {c['total']}：\n{by_kind}\n{err_line}\n\n{table}\n")


def _resolve_report(state: SessionState, source: str | None, protocol: str | None):
    hits = [(k, r) for k, r in state.reports.items()
            if source is None or k == source or k.startswith(source + "|")]
    if protocol:
        hits = [(k, r) for k, r in hits if r.protocol == protocol]
    if not hits:
        raise ProtocolLockError(
            f"无匹配解码报告（source={source!r}, protocol={protocol!r}）；已有: "
            f"{sorted(state.reports)}"
        )
    if len(hits) > 1:
        srcs = {k.split("|")[0] for k, _ in hits}
        hint = ("请用 source 参数指定源"
                if len(srcs) > 1 else "请用 protocol 参数区分同源的多协议")
        raise ProtocolLockError(
            f"解码报告不唯一，{hint}: {[(k, r.protocol) for k, r in hits]}"
        )
    key, rep = hits[0]
    alias = key.split("|")[0]
    return key, alias, rep


def filter_events(state: SessionState, source: str | None, protocol: str | None,
                  kind: str | None, t_min: float | None, t_max: float | None,
                  has_errors: bool | None) -> list[DecodedEvent]:
    _key, _alias, report = _resolve_report(state, source, protocol)
    out = []
    for ev in report.events:
        if kind and ev.kind != kind:
            continue
        if t_min is not None and ev.t_start < t_min:
            continue
        if t_max is not None and ev.t_start > t_max:
            continue
        if has_errors is True and not ev.errors:
            continue
        if has_errors is False and ev.errors:
            continue
        out.append(ev)
    return out


def export_events(state: SessionState, fmt: str, path: str | None,
                  source: str | None, protocol: str | None = None) -> Path:
    _key, alias, report = _resolve_report(state, source, protocol)
    cap = state.capture_of(alias)
    ext = {"json": "json", "csv": "csv", "md": "md"}[fmt]
    p = Path(path) if path else store_path(state, cap.capture_id, f"events.{ext}")
    p.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        p.write_text(report_json(report), encoding="utf-8")
    elif fmt == "csv":
        p.write_text(report_csv_rows(report), encoding="utf-8")
    else:
        p.write_text(events_markdown(report.events), encoding="utf-8")
    state.artifacts.register(p, "export", f"源 {alias}：{report.counts()['total']} 事件（{fmt}）")
    return p


def store_path(state: SessionState, capture_id: str, name: str) -> Path:
    return state.artifacts.path_for(capture_id, name)


def _alias_of_report(state: SessionState, report: DecodeReport) -> str:
    for alias, r in state.reports.items():
        if r is report:
            return alias
    raise RuntimeError("报告不属于任何源")


def render_timing(state: SessionState, t_min, t_max, max_frames, dpi, source,
                  protocol: str | None = None) -> tuple[Path, str]:
    _key, alias, report = _resolve_report(state, source, protocol)
    lock = next((l for l in state.locks.values()
                 if l.source == alias and l.protocol == report.protocol), None)
    cap = state.capture_of(alias)
    digital = cap.digital
    if digital is None:
        assert lock is not None and lock.graph is not None
        if "slice" in lock.graph.nodes:
            # 模拟源（数字协议）：复用图求值取切片输出
            sl = evaluate(lock.graph, get_registry(), targets=["slice"],
                          sources={"apick": {"in": cap}})
            digital = sl["slice"]["out"]
        else:
            # 模拟直达协议（uplink，ADR-010）：渲染原始模拟 + 事件 span
            store = state.artifacts
            n_existing = len([a for a in store.items
                              if a.kind == "figure" and "timing" in a.path.name])
            p = store.path_for(cap.capture_id, f"timing_{n_existing + 1}.png")
            analog_plot(cap.analog, p, threshold=None, t_min=t_min, t_max=t_max,
                        dpi=dpi or 150, events=report.events,
                        title=f"{report.protocol} 突发时序 · 源 {alias} · {cap.capture_id}")
            store.register(p, "figure", f"突发时序图（源 {alias}，模拟+帧 span）")
            vis = [e for e in report.events if not e.kind.endswith(".warn")]
            shown = vis[: (max_frames or 60)]
            return p, events_markdown(shown)
    store = state.artifacts
    n_existing = len([a for a in store.items if a.kind == "figure" and "timing" in a.path.name])
    p = store.path_for(cap.capture_id, f"timing_{n_existing + 1}.png")
    timing_plot(digital, report.events, p, t_min=t_min, t_max=t_max,
                max_frames=max_frames, dpi=dpi or 150,
                title=f"{report.protocol} 解码时序 · 源 {alias} · {cap.capture_id}")
    store.register(p, "figure", f"时序图（源 {alias}，窗口 {t_min or '起点'}–{t_max or '终点'}）")
    vis = [e for e in report.events
           if (t_min is None or e.t_end >= t_min) and (t_max is None or e.t_start <= t_max)
           and not e.kind.endswith(".warn")]
    shown = vis[: (max_frames or 60)]
    return p, events_markdown(shown)


def render_analog(state: SessionState, channel, t_min, t_max, dpi, source) -> Path:
    alias = state.resolve_alias(source)
    cap = state.capture_of(alias)
    if not cap.analog:
        raise ProtocolLockError(f"源 `{alias}` 没有模拟通道（render_analog 仅用于模拟源）")
    chs = [c for c in cap.analog if (channel is None or c.name == channel)]
    if not chs:
        raise ProtocolLockError(f"模拟通道 {channel!r} 不存在；可用: {[c.name for c in cap.analog]}")
    store = state.artifacts
    tag = chs[0].name if len(chs) == 1 else "multi"
    n_existing = len([a for a in store.items if a.kind == "figure" and "analog" in a.path.name])
    p = store.path_for(cap.capture_id, f"analog_{tag}_{n_existing + 1}.png")
    lock = next((l for l in state.locks.values()
                 if l.source == alias), None)
    thr = (lock.params.get("threshold") if lock else None)
    analog_plot(chs, p, digital=None, threshold=thr, t_min=t_min, t_max=t_max, dpi=dpi or 150,
                title=f"模拟波形 · 源 {alias} · {cap.capture_id}")
    store.register(p, "figure", f"模拟波形（源 {alias}，{[c.name for c in chs]}）")
    return p


def graph_text(state: SessionState, source: str | None, protocol: str | None = None) -> str:
    hits = [(k, l) for k, l in state.locks.items()
            if source is None or k == source or k.startswith(source + "|")]
    if protocol:
        hits = [(k, l) for k, l in hits if l.protocol == protocol]
    if len(hits) != 1:
        raise ProtocolLockError(
            f"需要唯一协议锁（source={source!r}, protocol={protocol!r}）；候选: "
            f"{[(k, l.protocol) for k, l in hits]}"
        )
    return hits[0][1].graph.to_text()


def reset(state: SessionState) -> str:
    was = state.stage.value
    state.__init__()  # dataclass 重置
    return f"会话已重置（原阶段 {was} → DISCOVERY）。"


# ------------------------------------------------------- 工程档案（ADR-009）---

def list_profiles() -> str:
    from .profile import list_profiles as _ls

    items = _ls()
    if not items:
        return ("暂无工程档案。流程：lock_source/add_source → 各源 lock_protocol → "
                "`save_profile(name=…)` 固化；下次 `open_project` 一步直达 READY。")
    rows = ["| 档案 | 源 | 协议锁 | 说明 |", "|---|---|---|---|"]
    for it in items:
        rows.append(f"| `{it['name']}` | {it['sources']} | {it['locks']} | {it['description']} |")
    return "\n".join(rows) + "\n\n打开: `open_project(profile=…, files={别名: 路径})` → `run_decode`"


def save_profile(state: SessionState, name: str, description: str | None) -> str:
    """把当前会话的源定义与协议锁固化为 profiles/<name>.json。"""
    from .profile import ProfileSpec, LockSpec, SourceSpec, save_profile as _save

    if state.project is None or not state.project.entries:
        raise ProtocolLockError("先 lock_source（至少一个源）再保存档案")
    spec = ProfileSpec(
        name=name,
        description=description or "",
        sources=[SourceSpec(alias=e.alias, format=e.capture.meta.format_key,
                            options=dict(e.options))
                 for e in state.project.entries],
        locks=[LockSpec(source=l.source, protocol=l.protocol, params=dict(l.params))
               for l in state.locks.values()],
    )
    path = _save(spec)
    n_locks = len(spec.locks)
    return (f"✅ 工程档案已保存: `{path}`\n"
            f"- 源 {len(spec.sources)} 个（别名/格式/选项已固化）\n"
            f"- 协议锁 {n_locks} 个（协议/参数/通道角色已钉死）\n"
            f"- 文件路径不固化（每次采集不同）；档案可提交进固件仓库、跨机器/团队共享\n"
            f"\n下次会话: `open_project(profile=\"{name}\", files={{…}})` 一步直达 READY")


def open_project(state: SessionState, profile: str, files: dict) -> str:
    """按档案一步开工程：摄取各源 + 应用全部协议锁 → READY。"""
    from .profile import load_profile as _load

    if state.project is not None and state.project.entries:
        raise ProtocolLockError("当前会话已有源；如需换档案请先 reset_session")
    spec = _load(profile)
    missing = [s.alias for s in spec.sources if s.alias not in (files or {})]
    if missing:
        raise ProtocolLockError(
            f"档案要求源 {missing} 的文件路径（files={{别名: 路径}}）；"
            f"档案定义的源: {[s.alias for s in spec.sources]}"
        )
    sections = [f"## 工程档案 `{spec.name}` 已打开"
                + (f"（{spec.description}）" if spec.description else "")]
    for s in spec.sources:
        _add_entry(state, files[s.alias], s.format, {**s.options, "alias": s.alias})
        cap = state.project.find(s.alias).capture
        names = cap.channel_names()
        sections.append(
            f"- 源 `{s.alias}` [{cap.meta.format_key}]：数字 {len(names['digital'])} / "
            f"模拟 {len(names['analog'])} 通道，时长 {cap.duration:.6g}s"
        )
    applied = []
    for lk in spec.locks:
        try:
            plan, _g = lock_protocol(state, lk.protocol, dict(lk.params), lk.source)
        except ProtocolLockError as e:
            raise ProtocolLockError(
                f"档案协议锁应用失败（源 `{lk.source}`，{lk.protocol}）: {e}\n"
                f"→ 常见原因：探头接线与档案不符（通道集合变了）。"
                f"请核对接线，或修正档案后重试。"
            ) from e
        applied.append(f"`{lk.source}`🔒{lk.protocol}")
    if not spec.locks:
        state.stage = Stage.SOURCE_LOCKED
        sections.append("\n（档案无协议锁）下一步: lock_protocol → run_decode")
    else:
        sections.append("- 协议锁: " + ", ".join(applied))
        sections.append("\n下一步: `run_decode`（全部已锁源并行解码）")
    return "\n".join(sections)


def capabilities_text() -> str:
    fmts = "\n".join(f"- `{k}`: {v}" for k, v in SUPPORTED_FORMATS.items())
    planned = "\n".join(f"- `{k}`: {v}" for k, v in PLANNED_FORMATS.items())
    protos = []
    for name, c in PROTOCOL_CATALOG.items():
        ps = "; ".join(f"{k}={v}" for k, v in c["params"].items())
        protos.append(f"- **{name}**: 角色 {c['roles']}。{c['hint']}\n  参数: {ps}")
    return (
        "## 支持的采集格式（lock_source 的 format 可选值；默认自动嗅探）\n"
        f"{fmts}\n\n## 延后支持\n{planned}\n\n"
        "## 支持的解码协议（lock_protocol 的 protocol 值）\n" + "\n".join(protos) +
        "\n\n## 建议流程\n"
        "**重复调试（推荐）**：`list_profiles` 查看工程档案 → "
        "`open_project(profile=…, files={别名: 路径})` 一步直达 READY → `run_decode`\n"
        "**首次调试**：\n"
        "1. `lock_source(path=…)` 摄取第一个源 →\n"
        "2. `add_source(path=…)` 追加更多源（可重复；各源独立分析）→\n"
        "3. `describe_capture` 查看各源通道 →\n"
        "4. 每源 `lock_protocol(protocol=…, source=别名)` →\n"
        "5. `run_decode`（一次解码全部已锁源）→ `save_profile(name=…)` 固化，下次一步直达"
    )
