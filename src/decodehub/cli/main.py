"""decodehub 命令行（ADR-015）：项目配置驱动的 headless 解码。

    decodehub validate [CONFIG]              校验配置/档案/采集绑定（CI 首道防线）
    decodehub run [CONFIG] [选项]            开工程 → 解码 → 导出/渲染 → 运行索引
    decodehub diff A B                       两份 decoded.json 的事件流对比（回归）

CONFIG 缺省 = ./decodehub.toml。退出码：0 成功 / 1 语义失败（校验问题、
运行有失败采集集、diff 有差异）/ 2 用法错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import __version__
from ..acquisition.sniff import SUPPORTED_FORMATS
from ..app import runner, services
from ..app.config import (CONFIG_NAME, check_capture_coverage, expand_captures,
                          find_config, load_config)
from ..app.diffing import diff_files
from ..app.profile import validate_profile_dict
from ..shared.errors import DecodehubError


def _parse_capture_args(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        alias, sep, path = item.partition("=")
        if not sep or not alias.strip() or not path.strip():
            raise DecodehubError(
                f"--capture 需要 别名=路径 形式，实际: {item!r}"
            )
        out[alias.strip()] = path.strip()
    return out


# ---------------------------------------------------------------- validate ---

def cmd_validate(args) -> int:
    config = load_config(find_config(args.config))
    problems: list[str] = []
    print(f"配置: {config.path}")
    print(f"档案目录: {config.profiles_dir}  产物目录: {config.out_dir}")
    for run in config.runs.values():
        print(f"\n## run `{run.name}`")
        try:
            spec = config.resolve_profile(run)
        except DecodehubError as e:
            problems.append(f"runs.{run.name}: {e}")
            continue
        src = f"档案 {run.profile_name!r}" if run.profile_name else "内联 decode 定义"
        print(f"- 解码定义: {src}（源 {len(spec.sources)}，协议锁 {len(spec.locks)}）")
        # 已知格式/协议白名单复核（load_profile 只做结构校验）
        for p in validate_profile_dict(spec.to_dict(),
                                       known_formats=set(SUPPORTED_FORMATS),
                                       known_protocols=set(services.PROTOCOL_CATALOG)):
            problems.append(f"runs.{run.name}（{spec.name}）: {p}")
        try:
            check_capture_coverage(config, run, spec, None)
            sets = expand_captures(config, run)
        except DecodehubError as e:
            problems.append(f"runs.{run.name}: {e}")
            continue
        mode = "批量" if len(sets) > 1 else "单采集集"
        parts = [f"`{a}`×{_alias_count(sets, a)}" for a in run.captures]
        print(f"- 采集绑定（{mode}，共 {len(sets)} 集）: " + "; ".join(parts))
        if not spec.locks:
            print("- ⚠️ 无协议锁（该 run 只摄取不解码）")
    if problems:
        print("\n## ❌ 校验未通过\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    print("\n✅ 配置、档案与采集绑定全部有效")
    return 0


def _alias_count(sets, alias: str) -> int:
    return sum(1 for cs in sets if alias in cs.files)


# -------------------------------------------------------------------- run ---

def cmd_run(args) -> int:
    config = load_config(find_config(args.config))
    overrides = _parse_capture_args(args.capture)
    result = runner.run_config(
        config, run_name=args.run, capture_overrides=overrides,
        out_dir=Path(args.out) if args.out else None, fail_fast=args.fail_fast,
    )
    for o in result.outcomes:
        if o.ok:
            kinds = ", ".join(f"{k}×{v}" for k, v in sorted(o.by_kind.items())) or "无"
            print(f"✅ {o.label}: {o.total} 事件（{kinds}）→ {o.dir}")
        else:
            first = (o.error or "").strip().splitlines()
            print(f"❌ {o.label}: {first[0] if first else '未知错误'}")
    print(f"\n运行索引: {result.index_path}")
    print(f"运行汇总: {result.summary_path}")
    if result.failed:
        print(f"⚠️ {result.failed}/{len(result.outcomes)} 个采集集失败")
        return 1
    return 0


# ------------------------------------------------------------------- diff ---

def cmd_diff(args) -> int:
    rep = diff_files(args.a, args.b, max_show=args.max)
    text = rep.markdown()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"对比报告 → {args.out}")
    print(text)
    if rep.identical:
        print("✅ 一致")
        return 0
    print("❌ 存在差异")
    return 1


# ------------------------------------------------------------------ 装配 ---

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decodehub",
        description="采集数据统一解码平台 · headless CLI（配置驱动，见 docs/70-headless-cli.md）",
    )
    parser.add_argument("--version", action="version", version=f"decodehub {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="校验配置/档案/采集绑定（不解码）")
    p.add_argument("config", nargs="?", help=f"配置路径（缺省 ./{CONFIG_NAME}）")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("run", help="按配置运行：开工程 → 解码 → 导出/渲染 → 运行索引")
    p.add_argument("config", nargs="?", help=f"配置路径（缺省 ./{CONFIG_NAME}）")
    p.add_argument("--run", help="运行名（配置定义多个运行时必填）")
    p.add_argument("--capture", action="append", metavar="别名=路径",
                   help="覆盖/补充采集文件绑定（可重复；不做 glob 展开）")
    p.add_argument("--out", help="覆盖产物根目录")
    p.add_argument("--fail-fast", action="store_true", help="首个采集集失败即停止")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("diff", help="对比两份 decoded.json（同档案回归对比，忽略时间戳）")
    p.add_argument("a", help="A 的 decoded.json 路径")
    p.add_argument("b", help="B 的 decoded.json 路径")
    p.add_argument("--out", help="对比报告另存路径（Markdown）")
    p.add_argument("--max", type=int, default=8, help="最多展示的差异条数（默认 8）")
    p.set_defaults(fn=cmd_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("MPLBACKEND", "Agg")  # headless 渲染保险（plots 亦会设 Agg）
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except DecodehubError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
