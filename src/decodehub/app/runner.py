"""Headless 运行器（ADR-015）：ProjectConfig → 逐采集集 开工程 → 解码 → 导出/渲染 → 运行索引。

MCP 会话（services 状态机）面向 LLM 交互；本模块把同一批应用层用例
（ingest / lock_protocol / run_decode / export / render）编排成**无会话依赖**的
批处理：每个采集集一个独立 SessionState（memo/制品互不残留），产物落
`out_dir/<run>/<采集集label>/`，运行级写 index.md + summary.json，
采集集级恒写机器汇总 decoded.json（diff/CI 断言的输入）。

增量运行（ADR-025，`decodehub run --incremental`）：每个 sink（锁/管线报告）
按"依赖闭包指纹"（源文件 sha256 + 参数/链定义 + 命名与导出渲染配置 +
tool_version）判定新鲜度；未变的 sink 跳过（旧产物原地沿用），只重算受影响
的子图——锚依赖（downlink→uplink 的源文件）经闭包自动传播。指纹记录在采集集
目录的 manifest.json；tap 无法唯一定位的管线保守处理（恒重算、全量摄取）。

健壮性保证（部分重算不破坏产物集）：管线 sink 失效时 rerun 闭包**反向传播**
到其 tap 上游（锁/管线递归到根）强制重算，保证绑定上游总在；缺省导出名按
采集集**全部 sink 数**判定、图序号按全量 sink 序模拟——命名只由配置决定，
与本次实际重算的子集无关，重算前后文件名稳定、不覆盖跳过 sink 的旧图。

注意本模块**只调用 services 公共函数**，不修改它们——MCP 网关与 headless
两条入口共享同一应用层语义。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import __version__
from ..decode.bindings import get_binding
from ..render.artifacts import ArtifactStore
from ..shared.errors import ConfigError, DecodehubError, ProtocolLockError
from . import services
from .config import (EXPORT_FORMATS, CaptureSet, ProjectConfig, RunSpec,
                     check_capture_coverage, expand_captures)
from .profile import LockSpec, ProfileSpec
from .session import SessionState, make_lock_key, sink_name_conflict_problems

MANIFEST_NAME = "manifest.json"


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
    rerun_sinks: int = 0
    skipped_sinks: int = 0


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


# ------------------------------------------------------- 增量：依赖与指纹 ---

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lock_keys(spec_locks: list[LockSpec]) -> dict[str, LockSpec]:
    """锁键（源|实例名）→ LockSpec。构造规则只有 session.make_lock_key 一份。"""
    return {make_lock_key(lk.source, lk.name, lk.protocol): lk for lk in spec_locks}


def _lock_source_deps(spec_locks: list[LockSpec]) -> dict[str, set[str]]:
    """锁键 → 依赖的源别名闭包（锚依赖传递：downlink ⊇ uplink 的源，ADR-025）。"""
    by_key = _lock_keys(spec_locks)
    deps: dict[str, set[str]] = {}

    def closure(key: str) -> set[str]:
        if key in deps:
            return deps[key]
        lk = by_key[key]
        deps[key] = {lk.source}  # 占位防环
        out = {lk.source}
        sync = get_binding(lk.protocol).requires_sync
        if sync:
            v = (lk.params or {}).get(f"{sync}_source")
            anchor_key = None
            if v:
                if v in by_key:
                    anchor_key = v
                else:
                    hits = [k for k, c in by_key.items()
                            if c.source == v or (c.name or c.protocol) == v]
                    if len(hits) == 1:
                        anchor_key = hits[0]
            else:
                cands = [k for k, c in by_key.items()
                         if c.protocol == sync and k != key]
                if len(cands) == 1:
                    anchor_key = cands[0]
            if anchor_key:
                out |= closure(anchor_key)
        deps[key] = out
        return out

    for k in by_key:
        closure(k)
    return deps


def _sink_fingerprints(run: RunSpec, spec_locks: list[LockSpec],
                       cs_files: dict[str, Path]) -> tuple[dict[str, str],
                                                           dict[str, set[str]],
                                                           dict[str, str],
                                                           dict[str, str]]:
    """每把锁/每条管线的指纹、源依赖闭包、所属源、tap 上游 sink 键。

    指纹覆盖：依赖源文件哈希、锁参数/实例名、管线链定义、命名模板、
    导出/渲染配置、tool_version——任一变化即失效。tap 无法唯一定位的管线
    键记为 `*|名`（永不命中 manifest = 恒重算），依赖记全量源（保守摄取）。

    第 4 个返回值 `upstream_of`：管线 sink 键 → 其 tap 的上游 sink 键（锁或
    管线，已解析者才有）。供增量计划做 rerun 闭包反向传播（Bug 3）。
    """
    base = json.dumps({
        "tool_version": __version__,
        "naming": asdict(run.naming),
        "export": asdict(run.export) if run.export else None,
        "render": asdict(run.render) if run.render else None,
    }, sort_keys=True, ensure_ascii=False)

    by_key = _lock_keys(spec_locks)
    lock_deps = _lock_source_deps(spec_locks)
    needed = {a for d in lock_deps.values() for a in d}
    file_of = {alias: cs_files.get(alias) for alias in needed}

    def file_sig(alias: str) -> str:
        f = file_of.get(alias)
        sha = _file_sha256(f) if f and Path(f).is_file() else "missing"
        return f"{alias}:{sha}"

    fps: dict[str, str] = {}
    deps: dict[str, set[str]] = {}
    sink_src: dict[str, str] = {}
    upstream_of: dict[str, str] = {}
    for key, lk in by_key.items():
        parts = sorted(file_sig(a) for a in lock_deps[key])
        blob = json.dumps({"base": base, "protocol": lk.protocol, "name": lk.name,
                           "params": lk.params, "files": parts},
                          sort_keys=True, ensure_ascii=False)
        fps[key] = hashlib.sha256(blob.encode()).hexdigest()
        deps[key] = set(lock_deps[key])
        sink_src[key] = lk.source

    # 管线：依赖被 tap sink 的指纹（链上链自然传递）；定点迭代解析
    pending = dict(run.pipelines)
    while pending:
        progressed = False
        for pname, ps in list(pending.items()):
            upstream_key = None
            if ps.tap is None:
                if len(by_key) == 1:
                    upstream_key = next(iter(by_key))
            elif ps.tap in fps:
                upstream_key = ps.tap
            else:
                lock_hits = [k for k in by_key
                             if k.rsplit("|", 1)[1] == ps.tap
                             or k.rsplit("|", 1)[0] == ps.tap]
                if len(lock_hits) == 1:
                    upstream_key = lock_hits[0]
                else:
                    pipe_hits = [k for k in fps
                                 if k not in by_key
                                 and k.rsplit("|", 1)[1] == ps.tap]
                    if len(pipe_hits) == 1:
                        upstream_key = pipe_hits[0]
            if upstream_key is not None and upstream_key.startswith("*|"):
                continue  # 上游本身未解析，等下一轮
            if upstream_key is None:
                continue
            src = upstream_key.rsplit("|", 1)[0]
            key = f"{src}|{pname}"
            blob = json.dumps({"base": base, "pipeline": pname,
                               "tap_fp": fps[upstream_key], "chain": ps.chain},
                              sort_keys=True, ensure_ascii=False)
            fps[key] = hashlib.sha256(blob.encode()).hexdigest()
            deps[key] = set(deps.get(upstream_key, set()))
            sink_src[key] = src
            upstream_of[key] = upstream_key
            pending.pop(pname)
            progressed = True
        if not progressed:
            for pname, ps in pending.items():
                key = f"*|{pname}"
                blob = json.dumps({"base": base, "pipeline": pname, "tap": ps.tap,
                                   "chain": ps.chain, "unresolved": True},
                                  sort_keys=True, ensure_ascii=False)
                fps[key] = hashlib.sha256(blob.encode()).hexdigest()
                deps[key] = set(cs_files)
                sink_src[key] = "*"
            pending.clear()
    return fps, deps, sink_src, upstream_of


def _load_manifest(set_dir: Path) -> dict | None:
    p = set_dir / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


# ------------------------------------------------------------- 采集集执行 ---

def _report_entries(state: SessionState) -> list[tuple[str, str, str]]:
    """(锁键, 源别名, 协议) —— 报告遍历的统一顺序。"""
    out = []
    for key, r in state.reports.items():
        alias = key.rsplit("|", 1)[0]
        out.append((key, alias, r.protocol))
    return out


def _write_decoded_json(state: SessionState, set_dir: Path, run: str, label: str,
                        spec: ProfileSpec, config: ProjectConfig,
                        filename: str = "decoded.json",
                        extra_reports: list[dict] | None = None) -> Path:
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
    reports += list(extra_reports or [])
    doc = {
        "tool_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": str(config.path),
        "run": run,
        "label": label,
        "profile": spec.name,
        "reports": reports,
    }
    p = set_dir / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _run_capture_set(config: ProjectConfig, run: RunSpec, spec: ProfileSpec,
                     cs: CaptureSet, set_dir: Path,
                     incremental: bool = False) -> CaptureOutcome:
    set_dir.mkdir(parents=True, exist_ok=True)
    state = SessionState()
    state.artifacts = _FlatStore(base_dir=set_dir)

    # ---- 增量计划（ADR-025）：sink 指纹 vs manifest ----
    file_hashes = {str(f): _file_sha256(f) for f in set(cs.files.values())}
    fps, deps, _src, upstream_of = _sink_fingerprints(run, spec.locks, cs.files)
    old = _load_manifest(set_dir) if incremental else None
    old_sinks: dict = (old or {}).get("sinks", {})
    rerun: list[str] = []
    skipped: dict[str, dict] = {}
    for key, fp in fps.items():
        e = old_sinks.get(key)
        if (incremental and e and e.get("fingerprint") == fp
                and all((set_dir / n).is_file() for n in e.get("files", []))):
            skipped[key] = e
        else:
            rerun.append(key)
    rerun_set = set(rerun)

    # ---- Bug 3 修复：rerun 闭包反向传播 ----
    # 管线 sink 进 rerun（典型：其产物文件被删，被存在性检查判失效）时，它 tap
    # 的上游 sink（锁或管线，递归到根）必须强制重算——否则上游指纹未变被跳过、
    # 不 lock_protocol，bind_pipeline 无上游可 tap，管线报告与产物整体丢失。
    # 上游被强制重算后其旧产物会以同名刷新（命名稳定见下文 events/图序号），正常。
    if incremental:
        changed = True
        while changed:
            changed = False
            for pkey, upkey in upstream_of.items():
                if pkey in rerun_set and upkey in skipped:
                    del skipped[upkey]
                    rerun.append(upkey)
                    rerun_set.add(upkey)
                    changed = True  # 上游可能又是管线：继续向根传播直至不动点

    decoded_path = set_dir / run.naming.decoded
    old_decoded = None
    if incremental and skipped:
        try:
            old_decoded = json.loads(decoded_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old_decoded = None
        if old_decoded is None:  # 旧机器汇总缺失：无从拼接，退回全量
            rerun, skipped = list(fps), {}
            rerun_set = set(rerun)
    needed_aliases: set[str] = set()
    for key in rerun:
        needed_aliases |= deps.get(key, set())

    try:
        for s in spec.sources:
            if incremental and s.alias not in needed_aliases:
                continue  # 该源的 sink 全部跳过：连摄取都不做
            services.ingest(state, str(cs.files[s.alias]), s.format,
                            {**s.options, "alias": s.alias})
        for key, lk in _lock_keys(spec.locks).items():
            if key not in rerun_set:
                continue
            try:
                services.lock_protocol(state, lk.protocol, dict(lk.params),
                                        lk.source, name=lk.name or None)
            except DecodehubError as e:
                raise ProtocolLockError(
                    f"协议锁应用失败（锁 `{key}`）: {e}\n"
                    f"→ 常见原因：接线/通道与配置不符，或协议参数过时。"
                ) from e
        p_key_by_name = {k.rsplit("|", 1)[1]: k for k in fps
                         if k not in _lock_keys(spec.locks)}
        for pname, ps in run.pipelines.items():
            if incremental and p_key_by_name.get(pname) not in rerun_set:
                continue
            try:
                services.bind_pipeline(state, pname, ps.tap, ps.chain)
            except DecodehubError as e:
                raise ProtocolLockError(
                    f"管线 {pname!r} 绑定失败: {e}\n"
                    f"→ 常见原因：tap 的协议锁未应用、节点类型未注册或链上端口类型不匹配。"
                ) from e
        if rerun_set:
            services.run_decode(state, None, None)

        naming = run.naming
        ctx = {"label": cs.label, "run": run.name}
        decoded_name = naming.decoded.format(**ctx)

        # ---- 逐 sink 导出/渲染（只处理重算 sink）----
        export = run.export
        fmts = export.formats if export else []
        entries = _report_entries(state)
        # Bug 4 修复：“单报告 → events.<ext>”缺省命名的判断依据改为**该采集集
        # 的全部 sink 数**（指纹表长度，含跳过的）。会话内报告数 len(entries)
        # 在部分重算时只剩重算子集——只重算一把锁会退化成 events.<ext>，原
        # events-<源>-<协议>.<ext> 永不再生成、产物集损坏。全量 sink 数与会话
        # 无关，重算前后命名稳定（全量运行时两者相等，行为不变）。
        n_sinks = len(fps)
        sink_files: dict[str, list[str]] = {k: [] for k in rerun_set}
        for ext in [f for f in EXPORT_FORMATS if f in fmts]:  # 注册表派生序（ADR-019）
            for key, alias, proto in entries:
                if key not in rerun_set:
                    continue
                if export and (export.source or export.protocol):
                    if export.source and export.source != alias:
                        continue
                    if export.protocol and export.protocol != proto:
                        continue
                    name = f"events.{ext}"
                elif naming.events:
                    name = naming.events.format(source=alias, protocol=proto,
                                                ext=ext, **ctx)
                else:
                    name = (f"events.{ext}" if n_sinks == 1
                            else f"events-{alias}-{proto}.{ext}")
                services.export_events(state, ext, str(set_dir / name),
                                       source=alias, protocol=proto)
                sink_files[key].append(name)

        render = run.render
        if render and (render.timing or render.analog):
            # ---- {n} 图序号稳定性（与 Bug 4 同源的会话数依赖）----
            # 序号不能基于会话内 state.artifacts 计数：部分重算时从 1 重来，会
            # 覆盖/错位跳过 sink 的旧图。编号改为只由配置决定的确定性序：
            # - timing：每 sink 恰好一张图，{n} = sink 在**全量 sink 序**（fps
            #   顺序 = 锁声明序 + 管线声明序 = 全量运行 reports 的遍历序）中的
            #   1-based 序号。全量运行与旧实现逐字节一致（旧实现全量运行时的
            #   会话计数 == 全量计数），部分重算时每个 sink 也复现同名、互不
            #   覆盖。（不取“sink 自身图计数”：timing 每 sink 恒为 1，且缺省
            #   模板 timing_{n}.png 无 source 限定，多 sink 会互撞。）
            # - analog：图按源归属，全局序依赖各源通道布局（跳过的源未摄取、
            #   无从得知），故 {n} = **该源自身已重算图计数**（1-based）；缺省
            #   模板在 runner 侧补上 source 限定，不同源的模拟图永不重名。
            timing_n: dict[str, int] = {}
            n_fig = 0
            if render.timing:
                for skey in fps:
                    n_fig += 1
                    timing_n[skey] = n_fig
            timing_tpl = naming.timing or "timing_{n}.png"  # 缺省模板固定在 runner 侧
            per_channel = bool(naming.analog) and "{channel}" in naming.analog
            analog_seen: dict[str, int] = {}  # 源别名 → 该源本次已重算图数
            for key, alias, proto in entries:
                if key not in rerun_set or not render.timing:
                    continue
                timing_name = timing_tpl.format(source=alias, protocol=proto,
                                                n=timing_n[key], **ctx)
                services.render_timing(state, render.t_min, render.t_max,
                                       render.max_frames, render.dpi,
                                       source=alias, protocol=proto,
                                       filename=timing_name)
                sink_files[key].append(timing_name)
            if render.analog:
                for e in state.project.entries:
                    if not e.capture.analog:
                        continue
                    channels = ([c.name for c in e.capture.analog] if per_channel
                                else [None])
                    for ch in channels:
                        n_src = analog_seen.get(e.alias, 0) + 1
                        analog_seen[e.alias] = n_src
                        if naming.analog:
                            analog_name = naming.analog.format(source=e.alias, channel=ch,
                                                               n=n_src, **ctx)
                        else:
                            analog_name = (f"analog-{e.alias}-{ch}_{n_src}.png" if ch
                                           else f"analog-{e.alias}_{n_src}.png")
                        services.render_analog(state, ch, render.t_min, render.t_max,
                                               render.dpi, source=e.alias,
                                               filename=analog_name)
                        owners = [ok for ok in rerun_set
                                  if e.alias in deps.get(ok, set())] or list(rerun_set)
                        for ok in owners:
                            sink_files[ok].append(analog_name)

        total = sum(r.counts()["total"] for r in state.reports.values())
        n_err = sum(r.counts()["errors"] for r in state.reports.values())
        by_kind: dict = {}
        for r in state.reports.values():
            for k, v in r.counts()["by_kind"].items():
                by_kind[k] = by_kind.get(k, 0) + v

        # skipped sink：事件沿用旧 decoded.json，计数并入聚合
        skipped_reports: list[dict] = []
        if skipped and old_decoded:
            for key, e in skipped.items():
                src, name = key.rsplit("|", 1)
                for r in old_decoded.get("reports", []):
                    if r.get("source") == src and r.get("protocol") == name:
                        skipped_reports.append(r)
                        total += r.get("total", 0)
                        n_err += sum(1 for ev in r.get("events", []) if ev.get("errors"))
                        for k, v in r.get("by_kind", {}).items():
                            by_kind[k] = by_kind.get(k, 0) + v
                        break
        _write_decoded_json(state, set_dir, run.name, cs.label, spec, config,
                            filename=decoded_name, extra_reports=skipped_reports)

        # ---- manifest：重算 sink 记新指纹，跳过 sink 记沿用 ----
        sinks_manifest: dict[str, dict] = {}
        for key in rerun:
            counts = (state.reports[key].counts() if key in state.reports
                      else {"total": 0, "errors": 0, "by_kind": {}})
            sinks_manifest[key] = {
                "fingerprint": fps[key],
                "total": counts["total"], "errors": counts["errors"],
                "by_kind": counts["by_kind"],
                "files": sink_files.get(key, []),
                "status": "rerun",
            }
        for key, e in skipped.items():
            sinks_manifest[key] = {**e, "status": "skipped"}
        (set_dir / MANIFEST_NAME).write_text(json.dumps({
            "tool_version": __version__,
            "files": file_hashes,
            "sinks": sinks_manifest,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        files = sorted({n for names in sink_files.values() for n in names}
                       | {decoded_name})
        return CaptureOutcome(label=cs.label, dir=set_dir, ok=True, total=total,
                              errors=n_err, by_kind=by_kind, files=files,
                              rerun_sinks=len(rerun), skipped_sinks=len(skipped))
    except Exception as e:
        return CaptureOutcome(label=cs.label, dir=set_dir, ok=False, error=str(e))


def _by_kind_of(outcome_dir: Path, decoded_name: str = "decoded.json") -> dict:
    decoded = outcome_dir / decoded_name
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
    rows = ["| 采集集 | 结果 | 增量 | 事件 | 错误 | 按类型 | 产物 |",
            "|---|---|---|---|---|---|---|"]
    for o in result.outcomes:
        if o.ok:
            kinds = ", ".join(f"{k}×{v}" for k, v in
                              sorted(_by_kind_of(o.dir, run.naming.decoded).items())) or "无"
            inc = f"重算 {o.rerun_sinks} / 跳过 {o.skipped_sinks}"
            rows.append(f"| `{o.label}` | ✅ | {inc} | {o.total} | {o.errors} | {kinds} "
                        f"| [`{o.label}/`]({o.label}/) |")
        else:
            err = (o.error or "").replace("\n", " ").replace("|", "\\|")
            rows.append(f"| `{o.label}` | ❌ {err[:160]} | - | - | - | - | - |")
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
        f"每个采集集目录含 `{run.naming.decoded}`（机器可读全量事件，diff/CI 输入）"
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
             "errors": o.errors, "dir": o.dir.name, "files": o.files,
             "rerun_sinks": o.rerun_sinks, "skipped_sinks": o.skipped_sinks}
            for o in result.outcomes
        ],
    }
    result.summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def run_config(config: ProjectConfig, run_name: str | None = None,
               capture_overrides: dict[str, str] | None = None,
               out_dir: Path | None = None, fail_fast: bool = False,
               incremental: bool = False) -> RunResult:
    """执行一个运行（可批量，可增量）。采集集级失败不中断（fail_fast=True 除外），
    汇总进 index/summary；调用方据 RunResult.failed 决定退出码。"""
    run = config.resolve_run(run_name)
    spec = config.resolve_profile(run)
    # 管线名 vs 锁实例名预检（Bug 2/2b）：声明期失败，不等逐采集集报错。
    # （内联定义在 load_config 已查；此处覆盖档案引用的锁，重复查无害。）
    probs = sink_name_conflict_problems(
        [make_lock_key(lk.source, lk.name, lk.protocol) for lk in spec.locks],
        [(n, f"runs.{run.name}.pipelines.{n}") for n in run.pipelines])
    if probs:
        raise ConfigError(f"runs.{run.name}: 管线名声明冲突\n"
                          + "\n".join(f"- {p}" for p in probs))
    check_capture_coverage(config, run, spec, capture_overrides)
    sets = expand_captures(config, run, capture_overrides)

    root = Path(out_dir) if out_dir else (
        (config.dir / run.out_dir) if run.out_dir else config.out_dir)
    base = root / run.name
    result = RunResult(run=run.name, profile=spec.name,
                       index_path=base / run.naming.index.format(run=run.name),
                       summary_path=base / run.naming.summary.format(run=run.name))
    t0 = time.perf_counter()
    for cs in sets:
        set_rel = (run.set_dir.format(label=cs.label, run=run.name)
                   if run.set_dir else cs.label)
        outcome = _run_capture_set(config, run, spec, cs, base / set_rel,
                                   incremental=incremental)
        result.outcomes.append(outcome)
        if not outcome.ok and fail_fast:
            break
    _write_index(config, run, spec, result, time.perf_counter() - t0)
    return result
