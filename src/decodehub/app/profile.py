"""工程档案（Profile）：固化"源定义 + 各源协议锁"的持久化配置（ADR-009）。

适用场景：已定型的项目——调试 IO 有限且固定、录制仪器固定、天天重复抓取分析。
档案是纯 JSON（人可读、可提交进固件仓库、跨机器/团队共享）：

{
  "name": "gizmo-v3", "description": "…", "version": 1,
  "sources": [{"alias": "la", "format": "kingst_kvdat", "options": {…}}],
  "locks":   [{"source": "la", "protocol": "i2c",
               "params": {"scl": "D0", "sda": "D1"}}]
}

文件路径每次会话换（新采集），档案只固化不变的部分。
通道角色钉死在 params 里 → open_project 时若采集缺该通道立即报错（接线防线）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..shared.errors import DecodehubError

PROFILE_VERSION = 1


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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "created": self.created or time.strftime("%Y-%m-%dT%H:%M:%S"),
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


def save_profile(spec: ProfileSpec, dir: Path | None = None) -> Path:
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
        raise DecodehubError(
            f"工程档案不存在: {path}"
            + (f"；可用: {available}" if available else "（profiles 目录为空或不存在，先用 save_profile 创建）")
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        spec = ProfileSpec(
            name=data["name"],
            description=data.get("description", ""),
            version=int(data.get("version", 1)),
            created=data.get("created", ""),
            sources=[SourceSpec(alias=s["alias"], format=s.get("format"),
                                options=s.get("options", {}))
                     for s in data.get("sources", [])],
            locks=[LockSpec(source=l["source"], protocol=l["protocol"],
                            params=l.get("params", {}))
                   for l in data.get("locks", [])],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise DecodehubError(f"工程档案损坏: {path}: {e}") from e
    if spec.version > PROFILE_VERSION:
        raise DecodehubError(
            f"档案版本 {spec.version} 高于当前支持的 {PROFILE_VERSION}（由更新版本的工具创建）"
        )
    lock_sources = {l.source for l in spec.locks}
    known = {s.alias for s in spec.sources}
    unknown = lock_sources - known
    if unknown:
        raise DecodehubError(f"档案锁引用了未定义的源: {sorted(unknown)}")
    return spec


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
