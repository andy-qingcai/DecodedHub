"""Headless 运行器（ADR-015）：ProjectConfig → 逐采集集 开工程 → 解码 → 导出/渲染 → 运行索引。

MCP 会话（services 状态机）面向 LLM 交互；本模块把同一批应用层用例
（ingest / lock_protocol / run_decode / export / render）编排成**无会话依赖**的
批处理：每个采集集一个独立 SessionState（memo/制品互不残留），产物落
`out_dir/<run>/<采集集label>/`，运行级写 index.md + summary.json，
采集集级恒写机器汇总 decoded.json（diff/CI 断言的输入）。

注意本模块**只调用 services 公共函数**，不修改它们——MCP 网关与 headless
两条入口共享同一应用层语义。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..render.artifacts import ArtifactStore
from ..shared.errors import DecodehubError, ProtocolLockError
from . import services
from .config import CaptureSet, ProjectConfig, RunSpec, check_capture_coverage, expand_captures
from .profile import ProfileSpec
from .session import SessionState


class _FlatStore(ArtifactStore):
    """采集集目录已按 label 隔离，压平 capture_id 子目录（路径人可读）。"""

    def path_for(self, capture_id: str, name: str) -> Path:
        self.base.mkdir(parents=True, exist_ok=True)
        return self.base / name


@dataclass
class CaptureOutcome:
    label: str
    dir: Path
    ok: bool
    error: str | None = None
    total: int = 0
    errors: int = 0
    by_kind: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run: str
    profile: str
    index_path: Path
    summary_path: Path
    outcomes: list[CaptureOutcome] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


def _report_entries(state: SessionState) -> list[tuple[str, str, str]]:
    """(锁键, 源别名, 协议) —— 报告遍历的统一顺序。"""
    out = []
    for key, r in state.reports.items():
        alias = key.rsplit("|", 1)[0]
        out.append((key, alias, r.protocol))
    return out


def _write_decoded_json(state: SessionState, set_dir: Path, run: str, label: str,
                        spec: ProfileSpec, config: ProjectConfig) -> Path:
    reports = []
    for key, alias, proto in _report_entries(state):
        r = state.reports[key]
        reports.append({
            "source": alias,
            "protocol": proto,
            "params": r.params,
            **r.counts(),
            "events": [e.to_dict() for e in r.events],
        })
    doc = {
        "tool_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": str(config.path),
        "run": run,
        "label": label,
        "profile": spec.name,
        "reports": reports,
    }
    p = set_dir / "decoded.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _run_capture_set(config: ProjectConfig, run: RunSpec, spec: ProfileSpec,
                     cs: CaptureSet, set_dir: Path) -> CaptureOutcome:
    set_dir.mkdir(parents=True, exist_ok=True)
    state = SessionState()
    state.artifacts = _FlatStore(base_dir=set_dir)
    try:
        for s in spec.sources:
            services.ingest(state, str(cs.files[s.alias]), s.format,
                            {**s.options, "alias": s.alias})
        for lk in spec.locks:
            try:
                services.lock_protocol(state, lk.protocol, dict(lk.params), lk.source)
            except DecodehubError as e:
                raise ProtocolLockError(
                    f"协议锁应用失败（源 `{lk.source}`，{lk.protocol}）: {e}\n"
                    f"→ 常见原因：接线/通道与配置不符，或协议参数过时。"
                ) from e
        services.run_decode(state, None, None)

        files: list[str] = ["decoded.json"]
        _write_decoded_json(state, set_dir, run.name, cs.label, spec, config)

        export = run.export
        fmts = export.formats if export else []
        entries = _report_entries(state)
        for ext in [f for f in ("csv", "json", "md") if f in fmts]:
            for key, alias, proto in entries:
                if export and (export.source or export.protocol):
                    if export.source and export.source != alias:
                        continue
                    if export.protocol and export.protocol != proto:
                        continue
                    name = f"events.{ext}"
                else:
                    name = (f"events.{ext}" if len(entries) == 1
                            else f"events-{alias}-{proto}.{ext}")
                services.export_events(state, ext, str(set_dir / name),
                                       source=alias, protocol=proto)
                files.append(name)

        render = run.render
        if render and (render.timing or render.analog):
            for key, alias, proto in entries:
                if render.timing:
                    services.render_timing(state, render.t_min, render.t_max,
                                           render.max_frames, render.dpi,
                                           source=alias, protocol=proto)
            if render.analog:
                for e in state.project.entries:
                    if e.capture.analog:
                        services.render_analog(state, None, render.t_min, render.t_max,
                                               render.dpi, source=e.alias)
            files += sorted(a.path.name for a in state.artifacts.items
                            if a.kind == "figure")
        total = sum(r.counts()["total"] for r in state.reports.values())
        n_err = sum(r.counts()["errors"] for r in state.reports.values())
        by_kind: dict = {}
        for r in state.reports.values():
            for k, v in r.counts()["by_kind"].items():
                by_kind[k] = by_kind.get(k, 0) + v
        return CaptureOutcome(label=cs.label, dir=set_dir, ok=True, total=total,
                              errors=n_err, by_kind=by_kind, files=files)
    except Exception as e:
        return CaptureOutcome(label=cs.label, dir=set_dir, ok=False, error=str(e))


def _by_kind_of(outcome_dir: Path) -> dict:
    decoded = outcome_dir / "decoded.json"
    if not decoded.is_file():
        return {}
    try:
        doc = json.loads(decoded.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    by_kind: dict[str, int] = {}
    for r in doc.get("reports", []):
        for k, v in r.get("by_kind", {}).items():
            by_kind[k] = by_kind.get(k, 0) + v
    return by_kind


def _write_index(config: ProjectConfig, run: RunSpec, spec: ProfileSpec,
                 result: RunResult, seconds: float) -> None:
    rows = ["| 采集集 | 结果 | 事件 | 错误 | 按类型 | 产物 |", "|---|---|---|---|---|---|"]
    for o in result.outcomes:
        if o.ok:
            kinds = ", ".join(f"{k}×{v}" for k, v in sorted(_by_kind_of(o.dir).items())) or "无"
            rows.append(f"| `{o.label}` | ✅ | {o.total} | {o.errors} | {kinds} "
                        f"| [`{o.label}/`]({o.label}/) |")
        else:
            err = (o.error or "").replace("\n", " ").replace("|", "\\|")
            rows.append(f"| `{o.label}` | ❌ {err[:160]} | - | - | - | - |")
    lines = [
        f"# 解码运行 · {run.name}",
        "",
        f"- 档案: `{spec.name}`（decodehub {__version__}）",
        f"- 配置: `{config.path}`",
        f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}（耗时 {seconds:.1f}s）",
        f"- 结果: {len(result.outcomes) - result.failed}/{len(result.outcomes)} 成功",
        "",
        "\n".join(rows),
        "",
        "每个采集集目录含 `decoded.json`（机器可读全量事件，diff/CI 输入）"
        + ("、events.* 导出与渲染图" if any(o.files for o in result.outcomes) else "") + "。",
    ]
    result.index_path.parent.mkdir(parents=True, exist_ok=True)
    result.index_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "tool_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": str(config.path),
        "run": run.name,
        "profile": spec.name,
        "outcomes": [
            {"label": o.label, "ok": o.ok, "error": o.error, "total": o.total,
             "errors": o.errors, "dir": o.dir.name, "files": o.files}
            for o in result.outcomes
        ],
    }
    result.summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def run_config(config: ProjectConfig, run_name: str | None = None,
               capture_overrides: dict[str, str] | None = None,
               out_dir: Path | None = None, fail_fast: bool = False) -> RunResult:
    """执行一个运行（可批量）。采集集级失败不中断（fail_fast=True 除外），
    汇总进 index/summary；调用方据 RunResult.failed 决定退出码。"""
    run = config.resolve_run(run_name)
    spec = config.resolve_profile(run)
    check_capture_coverage(config, run, spec, capture_overrides)
    sets = expand_captures(config, run, capture_overrides)

    base = (out_dir or config.out_dir) / run.name
    result = RunResult(run=run.name, profile=spec.name,
                       index_path=base / "index.md", summary_path=base / "summary.json")
    t0 = time.perf_counter()
    for cs in sets:
        outcome = _run_capture_set(config, run, spec, cs, base / cs.label)
        result.outcomes.append(outcome)
        if not outcome.ok and fail_fast:
            break
    _write_index(config, run, spec, result, time.perf_counter() - t0)
    return result
