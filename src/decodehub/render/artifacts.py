"""制品登记：out/<capture_id>/ 确定性路径 + 幂等覆盖（ADR-006）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Artifact:
    path: Path
    kind: str  # figure | table | export
    desc: str
    meta: dict = field(default_factory=dict)


class ArtifactStore:
    def __init__(self, base_dir: str | Path = "out"):
        self.base = Path(base_dir)
        self.items: list[Artifact] = []

    def path_for(self, capture_id: str, name: str) -> Path:
        d = self.base / (capture_id or "capture")
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def register(self, path: str | Path, kind: str, desc: str, **meta) -> Artifact:
        art = Artifact(path=Path(path), kind=kind, desc=desc, meta=meta)
        self.items.append(art)
        return art

    def manifest_markdown(self) -> str:
        if not self.items:
            return "（暂无制品）"
        lines = ["| 制品 | 类型 | 说明 | 大小 |", "|---|---|---|---|"]
        for a in self.items:
            size = a.path.stat().st_size if a.path.exists() else 0
            size_h = f"{size / 1024:.0f} KB" if size >= 1024 else f"{size} B"
            lines.append(f"| `{a.path}` | {a.kind} | {a.desc} | {size_h} |")
        return "\n".join(lines)
