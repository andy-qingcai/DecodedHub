"""会话状态机（应用层）：DISCOVERY → SOURCE_LOCKED → READY（docs/30）。

多源工程（ADR-008 v1.2）：会话持有 Project 容器；**每源独立协议锁与解码报告**
（各源时间轴独立，互不影响——用户环境无法跨设备同步，不拆总线到多设备）。
合并/对齐保留为库能力（Project.merged），不进入工具流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from ..acquisition.project import Project
from ..decode.events import DecodeReport
from ..decode.graph import Graph
from ..render.artifacts import ArtifactStore
from ..shared.waves import Capture


class Stage(str, Enum):
    DISCOVERY = "DISCOVERY"
    SOURCE_LOCKED = "SOURCE_LOCKED"
    READY = "READY"


# ------------------------------------------------------- 锁键（ADR-023）---

LOCK_KEY_SEP = "|"


def make_lock_key(source: str, name: str | None = None, protocol: str = "") -> str:
    """锁键 `源|实例名`（实例名缺省 = 协议名；ADR-023）。

    锁/管线/报告全按此键索引（SessionState.locks/reports、manifest 与
    decoded.json），构造规则全库只有这一份——键一致性由构造入口保证。
    本模块放在 session 而非 services：锁键是会话状态的表示，且 config/
    profile 的声明期校验需要无重依赖地导入（services 拖整条解码/渲染链）。
    """
    return f"{source}{LOCK_KEY_SEP}{name or protocol}"


def name_constraint_problems(name: str, where: str,
                             what: str = "实例名") -> list[str]:
    """实例名/管线名的硬约束（Bug 6）：不能包含锁键分隔符 `|`。

    返回问题列表（空 = 通过）。含 `|` 的名字会让锁键/报告键按
    `源|名` 切分时错位（decoded.json 的 source 字段串味），所有入口
    （config/profile 声明期 + lock_protocol/bind_pipeline 运行期）一律拒绝。
    """
    if LOCK_KEY_SEP in name:
        return [f"{where}: {what} {name!r} 不能包含 '|' 字符"
                "（它是锁键 `源|名` 的分隔符，会导致报告键解析错位）"]
    return []


def duplicate_lock_key_problems(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """锁键查重（Bug 1）：pairs = [(锁键, 声明位置)] → 重复问题列表。

    ADR-023"绝不静默覆盖"同样适用于配置声明：同键锁在 load/validate 的
    声明期就报错，不等运行期把前一把锁挤掉。
    """
    seen: dict[str, str] = {}
    out: list[str] = []
    for key, where in pairs:
        if key in seen:
            out.append(f"{where}: 锁键 {key!r} 重复（与 {seen[key]} 同源同名，"
                       "后一把会静默覆盖前一把）；请换 name 区分同源多路")
        else:
            seen[key] = where
    return out


def sink_name_conflict_problems(lock_keys: Iterable[str],
                                pipeline_names: Iterable[tuple[str, str]]) -> list[str]:
    """管线名与锁实例名的声明期冲突（Bug 2/2b）。

    管线也是锁（键 = `源|管线名`，ADR-019/020）：与任何锁实例名同名即有
    报告覆盖/指纹歧义风险（tap 命中该锁所在源时必然覆盖），声明期一律拒绝；
    管线名含 `|` 同样在此拒绝。
    """
    inst_names = {k.rsplit(LOCK_KEY_SEP, 1)[1] for k in lock_keys}
    out: list[str] = []
    for pname, where in pipeline_names:
        out += name_constraint_problems(pname, where, what="管线名")
        if pname in inst_names:
            out.append(f"{where}: 管线名 {pname!r} 与锁实例名同名"
                       f"（管线键 `源|{pname}` 会覆盖该锁的报告）；请换一个管线名")
    return out


@dataclass
class ProtocolLock:
    """一个源的协议锁定：协议、参数、通道映射、解码图。

    name:         锁实例名（ADR-023）——同源同协议多路并存时区分用
                  （如一路 uart 钉 D0、另一路钉 D2 → name="uart1"/"uart2"）。
                  缺省 = 协议名（向后兼容）；锁键 = `源|name`；报告键随之。
    source_inputs: 图中需要注入 Capture 的节点 → 源别名（ADR-011 跨源注入；
                  常规协议为单映射，downlink 为双映射：上行子图 + 本源）。
    graph_kind:   图形状元数据（digital=数字直达 / sliced=模拟经切片 /
                  analog_direct=模拟直达解码 / fan_in=含锚协议子图扇入）——
                  呈现与回写按此分派，不得嗅探节点 id 字符串。
    """

    source: str
    protocol: str
    params: dict
    channel_map: dict
    graph: Graph
    source_inputs: dict = None
    graph_kind: str = "digital"
    name: str = ""

    def __post_init__(self) -> None:
        if self.source_inputs is None:
            self.source_inputs = {}

    @property
    def report_name(self) -> str:
        """报告键里的名字（协议域呈现名）= 实例名，缺省协议名。"""
        return self.name or self.protocol


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
