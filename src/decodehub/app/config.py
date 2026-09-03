"""声明式项目配置 decodehub.toml（ADR-015）：解析 → 校验 → 采集集展开。

定位：团队/CI 的**自上而下**配置源。工程档案（Profile，ADR-009）固化"源 + 协议锁"，
本配置在其上补齐档案刻意不固化的部分——采集文件绑定（路径/glob）、输出管线
（导出/渲染）、产物布局——使解码可脱离 LLM 会话由 `decodehub run` 一条命令完成。

格式（所有相对路径相对本文件所在目录；字段严格校验，未知键报错防拼写）：

    version = 1

    [project]                    # 可选
    name = "gizmo-v3"
    profiles_dir = "profiles"    # 档案目录（默认 "profiles"）
    out_dir = "reports"          # 产物根目录（默认 "reports"）

    [runs.default]
    profile = "gizmo-v3"         # 引用档案；或用 [runs.default.decode] 内联
    [runs.default.captures]      # 源别名 → 文件路径或 glob
    la = "captures/*.kvdat"
    [runs.default.export]        # 可选；formats ⊆ csv/json/md（json 机器汇总恒产生）
    formats = ["csv", "md"]
    [runs.default.render]        # 可选
    timing = true

批量语义（expand_captures）：glob 命中 N>1 的别名为主变量，命中 1 的别名广播；
两个以上别名同时 >1 且数量不等 → 报错（宁可不猜，也不做隐式笛卡尔积）。
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..shared.errors import ConfigError
from .profile import LockSpec, ProfileSpec, SourceSpec, load_profile

CONFIG_NAME = "decodehub.toml"
EXPORT_FORMATS = ("csv", "json", "md")
_GLOB_CHARS = "*?["


@dataclass
class ExportStep:
    formats: list[str] = field(default_factory=list)
    source: str | None = None
    protocol: str | None = None


@dataclass
class RenderStep:
    timing: bool = False
    analog: bool = False
    t_min: float | None = None
    t_max: float | None = None
    max_frames: int = 60
    dpi: int = 150


@dataclass
class RunSpec:
    name: str
    profile_name: str | None = None      # 引用 profiles_dir 下的档案
    inline_spec: ProfileSpec | None = None  # 或内联解码定义（二选一）
    captures: dict[str, str] = field(default_factory=dict)  # 源别名 → 路径/glob
    export: ExportStep | None = None
    render: RenderStep | None = None


@dataclass
class CaptureSet:
    """一次解码的采集文件绑定：别名 → 文件（label 用作产物目录名）。"""

    label: str
    files: dict[str, Path]


@dataclass
class ProjectConfig:
    path: Path  # decodehub.toml 绝对/规范化路径
    version: int
    name: str
    description: str
    profiles_dir: Path
    out_dir: Path
    runs: dict[str, RunSpec]

    @property
    def dir(self) -> Path:
        return self.path.parent

    def resolve_run(self, name: str | None = None) -> RunSpec:
        if name:
            if name not in self.runs:
                raise ConfigError(
                    f"运行 {name!r} 不存在；配置定义的运行: {list(self.runs)}"
                )
            return self.runs[name]
        if len(self.runs) == 1:
            return next(iter(self.runs.values()))
        raise ConfigError(
            f"配置定义了 {len(self.runs)} 个运行，必须用 --run 指定: {list(self.runs)}"
        )

    def resolve_profile(self, run: RunSpec) -> ProfileSpec:
        if run.inline_spec is not None:
            return run.inline_spec
        return load_profile(run.profile_name, dir=self.profiles_dir)


# ------------------------------------------------------------ 解析 ---

def _table(d: dict, key: str, where: str, required: bool = False) -> dict | None:
    v = d.get(key)
    if v is None:
        if required:
            raise ConfigError(f"{where}: 缺少必填段 [{key}]")
        return None
    if not isinstance(v, dict):
        raise ConfigError(f"{where}: [{key}] 必须是表（TOML table）")
    return v


def _strict_keys(d: dict, known: set[str], where: str) -> None:
    for k in d:
        if k not in known:
            raise ConfigError(f"{where}: 未知字段 {k!r}（可用: {sorted(known)}）——多半是拼写错误")


def _get_str(d: dict, key: str, where: str) -> str | None:
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise ConfigError(f"{where}: {key} 必须是字符串，实际为 {v!r}")
    return v


def _get_float(d: dict, key: str, where: str) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{where}: {key} 必须是数字，实际为 {v!r}")
    return float(v)


def _get_int(d: dict, key: str, where: str, minimum: int | None = None) -> int | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}: {key} 必须是整数，实际为 {v!r}")
    if minimum is not None and v < minimum:
        raise ConfigError(f"{where}: {key} 必须 ≥ {minimum}，实际为 {v}")
    return int(v)


def _get_bool(d: dict, key: str, where: str) -> bool | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, bool):
        raise ConfigError(f"{where}: {key} 必须是布尔值，实际为 {v!r}")
    return v


def _parse_export(d: dict, where: str) -> ExportStep:
    _strict_keys(d, {"formats", "source", "protocol"}, where)
    formats = d.get("formats", [])
    if not isinstance(formats, list) or not all(isinstance(f, str) for f in formats):
        raise ConfigError(f"{where}: formats 必须是字符串数组")
    bad = [f for f in formats if f not in EXPORT_FORMATS]
    if bad:
        raise ConfigError(f"{where}: 未知导出格式 {bad}；可用: {list(EXPORT_FORMATS)}")
    return ExportStep(
        formats=list(formats),
        source=_get_str(d, "source", where),
        protocol=_get_str(d, "protocol", where),
    )


def _parse_render(d: dict, where: str) -> RenderStep:
    _strict_keys(d, {"timing", "analog", "t_min", "t_max", "max_frames", "dpi"}, where)
    return RenderStep(
        timing=bool(_get_bool(d, "timing", where)),
        analog=bool(_get_bool(d, "analog", where)),
        t_min=_get_float(d, "t_min", where),
        t_max=_get_float(d, "t_max", where),
        max_frames=_get_int(d, "max_frames", where, minimum=1) or 60,
        dpi=_get_int(d, "dpi", where, minimum=50) or 150,
    )


def _parse_inline_decode(d: dict, run_name: str, where: str) -> ProfileSpec:
    """[runs.X.decode] 内联解码定义 → ProfileSpec（与档案 JSON 同一模型）。"""
    _strict_keys(d, {"sources", "locks"}, where)
    spec = ProfileSpec(name=run_name)
    sources = _table(d, "sources", where, required=True)
    for alias, s in (sources or {}).items():
        w = f"{where}.sources.{alias}"
        if not isinstance(s, dict):
            raise ConfigError(f"{w}: 必须是表")
        _strict_keys(s, {"format", "options"}, w)
        opts = s.get("options", {})
        if not isinstance(opts, dict):
            raise ConfigError(f"{w}: options 必须是表")
        fmt = _get_str(s, "format", w)
        spec.sources.append(SourceSpec(alias=alias, format=fmt, options=dict(opts)))
    locks = _table(d, "locks", where) or {}
    for alias, l in locks.items():
        w = f"{where}.locks.{alias}"
        if not isinstance(l, dict):
            raise ConfigError(f"{w}: 必须是表")
        _strict_keys(l, {"protocol", "params"}, w)
        proto = _get_str(l, "protocol", w)
        if not proto:
            raise ConfigError(f"{w}: 缺少 protocol")
        params = l.get("params", {})
        if not isinstance(params, dict):
            raise ConfigError(f"{w}: params 必须是表")
        spec.locks.append(LockSpec(source=alias, protocol=proto, params=dict(params)))
    return spec


def _parse_run(name: str, d: dict) -> RunSpec:
    where = f"runs.{name}"
    _strict_keys(d, {"profile", "decode", "captures", "export", "render"}, where)
    profile_name = _get_str(d, "profile", where)
    decode = _table(d, "decode", where)
    if profile_name and decode:
        raise ConfigError(f"{where}: profile 与 decode 内联定义二选一")
    if not profile_name and not decode:
        raise ConfigError(f"{where}: 需要 profile（引用档案）或 [ {where}.decode ]（内联定义）")

    captures_t = _table(d, "captures", where, required=True)
    captures: dict[str, str] = {}
    for alias, pat in (captures_t or {}).items():
        if not isinstance(pat, str) or not pat:
            raise ConfigError(f"{where}.captures.{alias}: 必须是非空路径/glob 字符串")
        captures[alias] = pat

    export_t = _table(d, "export", where)
    render_t = _table(d, "render", where)
    return RunSpec(
        name=name,
        profile_name=profile_name,
        inline_spec=_parse_inline_decode(decode, name, f"{where}.decode") if decode else None,
        captures=captures,
        export=_parse_export(export_t, f"{where}.export") if export_t else None,
        render=_parse_render(render_t, f"{where}.render") if render_t else None,
    )


def load_config(path: str | Path | None = None) -> ProjectConfig:
    """解析并校验 decodehub.toml。path 缺省 = ./decodehub.toml。"""
    p = Path(path) if path else Path(CONFIG_NAME)
    if p.is_dir():
        p = p / CONFIG_NAME
    if not p.is_file():
        raise ConfigError(
            f"项目配置不存在: {p}\n"
            f"（在项目根目录创建 {CONFIG_NAME}，或用参数指定路径；格式见 docs/70-headless-cli.md）"
        )
    p = p.resolve()
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML 语法错误: {p}: {e}") from e

    _strict_keys(raw, {"version", "project", "runs"}, "配置顶层")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(f"version 必须是整数，实际为 {version!r}")
    if version != 1:
        raise ConfigError(f"不支持的配置版本 {version}（当前支持 version = 1）")

    proj = _table(raw, "project", "配置顶层") or {}
    _strict_keys(proj, {"name", "description", "profiles_dir", "out_dir"}, "[project]")

    runs_raw = raw.get("runs")
    if not isinstance(runs_raw, dict) or not runs_raw:
        raise ConfigError("缺少 [runs.*]：至少定义一个运行（如 [runs.default]）")
    runs: dict[str, RunSpec] = {}
    for name, rd in runs_raw.items():
        if not isinstance(rd, dict):
            raise ConfigError(f"runs.{name}: 必须是表")
        runs[name] = _parse_run(name, rd)

    return ProjectConfig(
        path=p,
        version=version,
        name=_get_str(proj, "name", "[project]") or p.parent.name,
        description=_get_str(proj, "description", "[project]") or "",
        profiles_dir=(p.parent / (_get_str(proj, "profiles_dir", "[project]") or "profiles")),
        out_dir=(p.parent / (_get_str(proj, "out_dir", "[project]") or "reports")),
        runs=runs,
    )


def find_config(explicit: str | Path | None = None) -> Path:
    """CLI 缺省配置发现：显式路径 → cwd/decodehub.toml。"""
    if explicit:
        return Path(explicit)
    cand = Path.cwd() / CONFIG_NAME
    if cand.is_file():
        return cand
    raise ConfigError(
        f"当前目录没有 {CONFIG_NAME}；请传入配置路径，或 cd 到项目根目录"
    )


# ------------------------------------------------------- 采集集展开（P1）---

def expand_captures(config: ProjectConfig, run: RunSpec,
                    overrides: dict[str, str] | None = None) -> list[CaptureSet]:
    """路径/glob → 采集集列表（批量语义见模块 docstring）。

    单集 label 取首个主文件词干；批量 label = `001_stem` 有序前缀（字典序展开，
    保证两次运行顺序一致）。
    """
    patterns = dict(run.captures)
    for alias, pat in (overrides or {}).items():
        patterns[alias] = pat

    per_alias: dict[str, list[Path]] = {}
    for alias, pat in patterns.items():
        pp = Path(pat)
        base = config.dir if not pp.is_absolute() else pp.anchor
        if pp.is_absolute():
            if any(ch in pat for ch in _GLOB_CHARS):
                raise ConfigError(
                    f"runs.{run.name}.captures.{alias}: 绝对路径不支持通配符（glob 相对配置文件目录解析）: {pat}"
                )
            files = [pp] if pp.is_file() else []
        elif any(ch in pat for ch in _GLOB_CHARS):
            files = sorted(f for f in config.dir.glob(pat) if f.is_file())
        else:
            files = [config.dir / pp] if (config.dir / pp).is_file() else []
        per_alias[alias] = files

    empty = [a for a, fs in per_alias.items() if not fs]
    if empty:
        detail = "; ".join(
            f"{a} → {patterns[a]}（相对 {config.dir}）" for a in empty
        )
        raise ConfigError(f"采集文件未命中: {detail}")

    base_n = max(len(fs) for fs in per_alias.values())
    mismatched = {a: len(fs) for a, fs in per_alias.items() if 1 < len(fs) != base_n}
    if mismatched:
        raise ConfigError(
            f"批量采集数量不一致: {mismatched}（主变量 {base_n} 个）。"
            f"数量为 1 的别名会自动广播；>1 的别名数量必须一致——"
            f"或拆成多个 run / 用 --capture 显式指定"
        )
    varying = [a for a, fs in per_alias.items() if len(fs) == base_n > 1]

    sets: list[CaptureSet] = []
    for i in range(base_n):
        files = {a: (fs[0] if len(fs) == 1 else fs[i]) for a, fs in per_alias.items()}
        if base_n == 1:
            label = Path(next(iter(files.values()))).stem
        else:
            lead = Path(files[varying[0]]).stem
            label = f"{i + 1:03d}_{lead}"
        sets.append(CaptureSet(label=label, files=files))
    return sets


def check_capture_coverage(config: ProjectConfig, run: RunSpec,
                           spec: ProfileSpec, overrides: dict[str, str] | None) -> None:
    """采集别名必须恰好覆盖档案/内联定义的源（缺失在开工程前报出，信息更可操作）。"""
    patterns = dict(run.captures)
    for alias in (overrides or {}):
        patterns[alias] = overrides[alias]
    defined = {s.alias for s in spec.sources}
    unknown = sorted(set(patterns) - defined)
    if unknown:
        raise ConfigError(
            f"runs.{run.name}: captures 里的别名 {unknown} 未在源定义中；源定义: {sorted(defined)}"
        )
    missing = sorted(defined - set(patterns))
    if missing:
        raise ConfigError(
            f"runs.{run.name}: 源 {missing} 没有采集文件绑定"
            f"（在 [runs.{run.name}.captures] 里补，或运行时 --capture {missing[0]}=路径）"
        )


# 使 `python -m decodehub.app.config <path>` 可作快速语法检查
if __name__ == "__main__":  # pragma: no cover
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    for r in cfg.runs.values():
        n = len(expand_captures(cfg, r))
        print(f"run {r.name!r}: profile={r.profile_name or '(内联)'} 采集集 {n}")
    print("OK")
