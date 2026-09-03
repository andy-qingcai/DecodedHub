"""解码事件流对比（ADR-015 P1）：同档案两次运行的回归对比。

输入是 runner 产出的机器汇总 `decoded.json`（source/protocol → 事件序列）。
对比语义：**按报告键逐位对齐、忽略时间戳**——回归场景里两次采集的绝对时刻
必然不同，事件的内容序列（类型 + 全部协议字段 + 错误）一致即视为一致。

不做 LCS/对齐猜测：同档案同配置下解码是确定性的，序列错位本身就是需要
暴露的差异；逐位对比给出第一个分歧点的两侧上下文。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..shared.errors import ConfigError

_SIGNATURE_SKIP = {"t_start", "t_end"}  # 时间不参与内容签名


def load_decoded(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"decoded.json 不存在: {p}（先 decodehub run 生成；diff 以它为输入）")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"不是合法 JSON: {p}: {e}") from e
    if not isinstance(data, dict) or "reports" not in data:
        raise ConfigError(f"缺少 reports 字段（不是 decodehub 的 decoded.json）: {p}")
    return data


def _report_key(r: dict) -> str:
    return f"{r.get('source', '?')}|{r.get('protocol', '?')}"


def _signature(ev: dict) -> str:
    payload = {k: v for k, v in ev.items() if k not in _SIGNATURE_SKIP}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _short(ev: dict) -> str:
    s = f"{ev.get('kind', '?')} {ev.get('label', '')}".strip()
    if ev.get("errors"):
        s += f" ⚠{ev['errors']}"
    return s


@dataclass
class DiffReport:
    identical: bool
    a_path: str
    b_path: str
    sections: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        head = (f"## 解码结果对比\n- A: `{self.a_path}`\n- B: `{self.b_path}`\n\n")
        if self.identical:
            return head + "✅ **完全一致**（全部报告键的事件内容序列逐位相同，时间戳不参与对比）\n"
        return head + "\n\n".join(self.sections) + "\n"


def diff_decoded(a: dict, b: dict, a_path: str = "A", b_path: str = "B",
                 max_show: int = 8) -> DiffReport:
    rep = DiffReport(identical=True, a_path=a_path, b_path=b_path)
    ra = {_report_key(r): r for r in a.get("reports", [])}
    rb = {_report_key(r): r for r in b.get("reports", [])}

    only_a = sorted(set(ra) - set(rb))
    only_b = sorted(set(rb) - set(ra))
    if only_a:
        rep.identical = False
        rep.sections.append(f"### 仅 A 有的报告: {only_a}")
    if only_b:
        rep.identical = False
        rep.sections.append(f"### 仅 B 有的报告: {only_b}")

    for key in sorted(set(ra) & set(rb)):
        ea, eb = ra[key].get("events", []), rb[key].get("events", [])
        sa = [_signature(e) for e in ea]
        sb = [_signature(e) for e in eb]
        n = max(len(sa), len(sb))
        diffs = [i for i in range(n)
                 if (i >= len(sa)) or (i >= len(sb)) or sa[i] != sb[i]]
        if not diffs:
            rep.sections.append(
                f"### `{key}`: ✅ 一致（{len(ea)} 事件）"
            )
            continue
        rep.identical = False
        by_kind_a: dict[str, int] = {}
        by_kind_b: dict[str, int] = {}
        for e in ea:
            by_kind_a[e.get("kind", "?")] = by_kind_a.get(e.get("kind", "?"), 0) + 1
        for e in eb:
            by_kind_b[e.get("kind", "?")] = by_kind_b.get(e.get("kind", "?"), 0) + 1
        kinds = sorted(set(by_kind_a) | set(by_kind_b))
        rows = ["| 类型 | A | B |", "|---|---|---|"]
        rows += [f"| {k} | {by_kind_a.get(k, 0)} | {by_kind_b.get(k, 0)} |" for k in kinds]
        rows.append(f"| 合计 | {len(ea)} | {len(eb)} |")

        lines = [f"### `{key}`: ❌ {len(diffs)} 处不同（首个在序号 {diffs[0] + 1}，共 A {len(ea)} / B {len(eb)} 事件）",
                 "\n".join(rows), ""]
        for i in diffs[:max_show]:
            lines.append(f"- 序号 {i + 1}:")
            lines.append(f"  - A: {_short(ea[i])}" if i < len(ea) else "  - A: （无）")
            lines.append(f"  - B: {_short(eb[i])}" if i < len(eb) else "  - B: （无）")
        if len(diffs) > max_show:
            lines.append(f"- …其余 {len(diffs) - max_show} 处略")
        rep.sections.append("\n".join(lines))
    return rep


def diff_files(path_a: str | Path, path_b: str | Path, max_show: int = 8) -> DiffReport:
    return diff_decoded(load_decoded(path_a), load_decoded(path_b),
                        a_path=str(path_a), b_path=str(path_b), max_show=max_show)
