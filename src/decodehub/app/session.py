"""会话状态机（应用层）：DISCOVERY → SOURCE_LOCKED → READY（docs/30）。

多源工程（ADR-008 v1.2）：会话持有 Project 容器；**每源独立协议锁与解码报告**
（各源时间轴独立，互不影响——用户环境无法跨设备同步，不拆总线到多设备）。
合并/对齐保留为库能力（Project.merged），不进入工具流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..acquisition.project import Project
from ..decode.events import DecodeReport
from ..decode.graph import Graph
from ..render.artifacts import ArtifactStore
from ..shared.waves import Capture


class Stage(str, Enum):
    DISCOVERY = "DISCOVERY"
    SOURCE_LOCKED = "SOURCE_LOCKED"
    READY = "READY"


@dataclass
class ProtocolLock:
    """一个源的协议锁定：协议、参数、通道映射、解码图。

    source_inputs: 图中需要注入 Capture 的节点 → 源别名（ADR-011 跨源注入；
    常规协议为单映射，downlink 为双映射：上行子图 + 本源）。
    graph_kind:   图形状元数据（digital=数字直达 / sliced=模拟经切片 /
                  analog_direct=模拟直达解码 / downlink=含上行锚子图）——
                  呈现与回写按此分派，不得嗅探节点 id 字符串。
    """

    source: str
    protocol: str
    params: dict
    channel_map: dict
    graph: Graph
    source_inputs: dict = None
    graph_kind: str = "digital"

    def __post_init__(self) -> None:
        if self.source_inputs is None:
            self.source_inputs = {}


@dataclass
class SessionState:
    stage: Stage = Stage.DISCOVERY
    project: Project | None = None
    locks: dict[str, ProtocolLock] = field(default_factory=dict)   # 源|协议 → lock
    reports: dict[str, DecodeReport] = field(default_factory=dict)  # 源|协议 → 最近报告
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    # 图求值 memo 缓存（锁键 → 节点 id → 输出；docs/30：改参数重解码只重算参数
    # 变化的节点，上游 pick/slice 命中缓存）。锁替换/解锁时同步淘汰。
    memos: dict[str, dict] = field(default_factory=dict)

    # ---- 源访问 ----------------------------------------------------------

    def source_aliases(self) -> list[str]:
        return [e.alias for e in self.project.entries] if self.project else []

    def single_alias(self) -> str | None:
        """恰好一个源时返回其别名（工具参数缺省值）；否则 None。"""
        aliases = self.source_aliases()
        return aliases[0] if len(aliases) == 1 else None

    def resolve_alias(self, alias: str | None) -> str:
        """别名缺省解析：唯一源 → 它；多源必须显式。"""
        if alias:
            if self.project is None or alias not in self.source_aliases():
                raise ValueError(f"源别名不存在: {alias!r}；可用: {self.source_aliases()}")
            return alias
        if self.project and len(self.project.entries) == 1:
            return self.project.entries[0].alias
        raise ValueError(f"有 {len(self.source_aliases())} 个源，必须指定 source；可用: {self.source_aliases()}")

    def capture_of(self, alias: str | None) -> Capture:
        if self.project is None or not self.project.entries:
            raise RuntimeError("会话无采集源（先 lock_source）")
        return self.project.find(self.resolve_alias(alias)).capture

    def report_of(self, alias: str | None) -> DecodeReport:
        """兼容入口：键=源 或 源|协议；多协议时报错引导（服务层用 _resolve_report）。"""
        if not self.reports:
            raise RuntimeError("尚无解码报告（先 run_decode）")
        hits = [(k, r) for k, r in self.reports.items()
                if alias is None or k == alias or k.startswith(alias + "|")]
        if len(hits) == 1:
            return hits[0][1]
        raise ValueError(f"报告不唯一（{[k for k, _ in hits] or '无匹配'}）；"
                         f"请用 源|协议 键精确指定")

    def lock_of(self, alias: str | None) -> ProtocolLock:
        if not self.locks:
            raise RuntimeError("尚未锁定协议（先 lock_protocol）")
        hits = [l for k, l in self.locks.items()
                if alias is None or k == alias or l.source == alias]
        if len(hits) == 1:
            return hits[0]
        raise ValueError(f"协议锁不唯一（{sorted(self.locks)}）；请用 源|协议 键")
