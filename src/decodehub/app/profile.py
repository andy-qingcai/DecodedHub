"""工程档案（Profile）：固化"源定义 + 各源协议锁"的持久化配置（ADR-009）。

适用场景：已定型的项目——调试 IO 有限且固定、录制仪器固定、天天重复抓取分析。
档案是纯 JSON（人可读、可提交进固件仓库、跨机器/团队共享）：

{
  "name": "gizmo-v3", "description": "…", "version": 1,
  "tool_version": "0.1.0",
  "sources": [{"alias": "la", "format": "kingst_kvdat", "options": {…}}],
  "locks":   [{"source": "la", "protocol": "i2c",
               "params": {"scl": "D0", "sda": "D1"}}]
}

文件路径每次会话换（新采集），档案只固化不变的部分。
通道角色钉死在 params 里 → open_project 时若采集缺该通道立即报错（接线防线）。

ADR-014 起：`validate_profile_dict` 提供字段级精确校验（手写档案 / CLI validate /
测试共用）；`tool_version` 记录保存时的 decodehub 版本（升级后排查行为变化的锚点）。
JSON Schema 见 schemas/profile.v1.schema.json（IDE 校验/补全用）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..shared.errors import ConfigError, DecodehubError

PROFILE_VERSION = 1

_TOP_KEYS = {"name", "description", "version", "created", "tool_version", "sources", "locks"}
_SOURCE_KEYS = {"alias", "format", "options"}
_LOCK_KEYS = {"source", "protocol", "params"}


def profiles_dir() -> Path:
    return Path(os.environ.get("DECODEHUB_PROFILES_DIR", "profiles"))


@dataclass
class SourceSpec:
    alias: str
    format: str | None = None
    options: dict = field(default_factory=dict)


@dataclass
class LockSpec:
    source: str
    protocol: str
    params: dict = field(default_factory=dict)


@dataclass
class ProfileSpec:
    name: str
    description: str = ""
    sources: list[SourceSpec] = field(default_factory=list)
    locks: list[LockSpec] = field(default_factory=list)
    created: str = ""
    version: int = PROFILE_VERSION
    tool_version: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "created": self.created or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool_version": self.tool_version,
            "sources": [
                {"alias": s.alias, "format": s.format, "options": s.options}
                for s in self.sources
            ],
            "locks": [
                {"source": l.source, "protocol": l.protocol, "params": l.params}
                for l in self.locks
            ],
        }


def _slug(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]+", "_", name).strip("_") or "profile"


# ------------------------------------------------------------ 校验（ADR-014）---

def validate_profile_dict(data: dict, *, known_formats: set[str] | None = None,
                          known_protocols: set[str] | None = None) -> list[str]:
    """档案字典的字段级校验，返回问题列表（空 = 通过）。

    独立于解析存在：手写档案、`decodehub validate`、测试共用同一套规则；
    known_formats/known_protocols 由调用方按需传入（避免本模块反向依赖
    acquisition/services）。
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["档案顶层必须是 JSON 对象"]

    for k in data:
        if k not in _TOP_KEYS:
            problems.append(f"未知字段 {k!r}（可用: {sorted(_TOP_KEYS)}）——多半是拼写错误")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append('"name" 必须是非空字符串')
    if "version" in data and not isinstance(data["version"], int):
        problems.append('"version" 必须是整数')
    for k in ("description", "created", "tool_version"):
        if k in data and not isinstance(data[k], str):
            problems.append(f'"{k}" 必须是字符串')

    aliases: set[str] = set()
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        problems.append('"sources" 必须是数组')
    else:
        for i, s in enumerate(sources):
            where = f"sources[{i}]"
            if not isinstance(s, dict):
                problems.append(f"{where}: 必须是对象")
                continue
            for k in s:
                if k not in _SOURCE_KEYS:
                    problems.append(f"{where}: 未知字段 {k!r}（可用: {sorted(_SOURCE_KEYS)}）")
            alias = s.get("alias")
            if not isinstance(alias, str) or not alias.strip():
                problems.append(f'{where}: 缺少非空 "alias"')
            elif alias in aliases:
                problems.append(f"{where}: 源别名 {alias!r} 重复")
            else:
                aliases.add(alias)
            fmt = s.get("format")
            if fmt is not None:
                if not isinstance(fmt, str):
                    problems.append(f'{where}: "format" 必须是字符串（省略 = 自动嗅探）')
                elif known_formats is not None and fmt not in known_formats:
                    problems.append(f'{where}: 未知格式 {fmt!r}；可用: {sorted(known_formats)}')
            if "options" in s and not isinstance(s["options"], dict):
                problems.append(f'{where}: "options" 必须是对象')

    locks = data.get("locks", [])
    if not isinstance(locks, list):
        problems.append('"locks" 必须是数组')
    else:
        for i, l in enumerate(locks):
            where = f"locks[{i}]"
            if not isinstance(l, dict):
                problems.append(f"{where}: 必须是对象")
                continue
            for k in l:
                if k not in _LOCK_KEYS:
                    problems.append(f"{where}: 未知字段 {k!r}（可用: {sorted(_LOCK_KEYS)}）")
            src = l.get("source")
            if not isinstance(src, str) or not src:
                problems.append(f'{where}: 缺少非空 "source"')
            elif aliases and src not in aliases:
                problems.append(f'{where}: "source" {src!r} 未在 sources 中定义')
            proto = l.get("protocol")
            if not isinstance(proto, str) or not proto:
                problems.append(f'{where}: 缺少非空 "protocol"')
            elif known_protocols is not None and proto not in known_protocols:
                problems.append(f'{where}: 未知协议 {proto!r}；可用: {sorted(known_protocols)}')
            if "params" in l and not isinstance(l["params"], dict):
                problems.append(f'{where}: "params" 必须是对象')
    return problems


# ------------------------------------------------------------ 存取 ---

def save_profile(spec: ProfileSpec, dir: Path | None = None) -> Path:
    from .. import __version__

    if not spec.tool_version:
        spec.tool_version = __version__
    d = dir or profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slug(spec.name)}.json"
    path.write_text(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_profile(name: str, dir: Path | None = None) -> ProfileSpec:
    d = dir or profiles_dir()
    path = d / f"{_slug(name)}.json"
    if not path.is_file():
        available = [p.stem for p in d.glob("*.json")] if d.is_dir() else []
        raise ConfigError(
            f"工程档案不存在: {path}"
            + (f"；可用: {available}" if available else "（profiles 目录为空或不存在，先用 save_profile 创建）")
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"工程档案不是合法 JSON: {path}: {e}") from e
    problems = validate_profile_dict(data)
    if problems:
        raise ConfigError(
            f"工程档案无效: {path}\n" + "\n".join(f"- {p}" for p in problems)
        )
    if int(data.get("version", 1)) > PROFILE_VERSION:
        raise ConfigError(
            f"档案版本 {data['version']} 高于当前支持的 {PROFILE_VERSION}（由更新版本的工具创建）"
        )
    try:
        return ProfileSpec(
            name=data["name"],
            description=data.get("description", ""),
            version=int(data.get("version", 1)),
            created=data.get("created", ""),
            tool_version=data.get("tool_version", ""),
            sources=[SourceSpec(alias=s["alias"], format=s.get("format"),
                                options=s.get("options", {}))
                     for s in data.get("sources", [])],
            locks=[LockSpec(source=l["source"], protocol=l["protocol"],
                            params=l.get("params", {}))
                   for l in data.get("locks", [])],
        )
    except (KeyError, TypeError, ValueError) as e:  # 防御：校验器遗漏的结构问题
        raise DecodehubError(f"工程档案损坏: {path}: {e!r}") from e


def list_profiles(dir: Path | None = None) -> list[dict]:
    d = dir or profiles_dir()
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "name": data.get("name", p.stem),
                    "description": data.get("description", ""),
                    "sources": len(data.get("sources", [])),
                    "locks": len(data.get("locks", [])),
                })
            except (OSError, ValueError):
                out.append({"name": p.stem, "description": "（解析失败）",
                            "sources": 0, "locks": 0})
    return out
