#!/usr/bin/env python3
"""USB 描述符规格测试入口：加载 examples/ 下的 JSON 规格，解析样例载荷。

用法（在仓库根目录）：
    .venv/bin/python examples/run_usb.py
    .venv/bin/python examples/run_usb.py <十六进制载荷>   # 解你自己的帧
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from decodehub.decode.fields import parse_payload

HERE = Path(__file__).resolve().parent

SPECS = {
    "device": json.loads((HERE / "usb_device_descriptor.json").read_text()),
    "config": json.loads((HERE / "usb_configuration_descriptor.json").read_text()),
}
SAMPLES = {k: v for k, v in json.loads((HERE / "usb_samples.json").read_text()).items()
           if not k.startswith("_")}


def dump(fields, indent=0) -> None:
    for f in fields:
        note = f.enum_label or ""
        if f.errors:
            note += (" " if note else "") + "⚠" + ",".join(f.errors)
        val = f.value.hex() if isinstance(f.value, bytes) else repr(f.value)
        head = f"{'    ' * indent}{f.id}"
        loc = f"@{f.offset_bits // 8:>3}B+{f.offset_bits % 8}bit w{f.width_bits:>3}"
        print(f"{head:<24} {loc:<16} {f.kind:<7} {val:<14} {note}".rstrip())
        dump(f.children, indent + 1)


def run(spec_key: str, hexstr: str) -> None:
    payload = bytes.fromhex("".join(hexstr.split()))
    print(f"\n── {spec_key}  {len(payload)} 字节 ──")
    dump(parse_payload(SPECS[spec_key], payload))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:  # 自带载荷：默认按设备描述符解，第二个参数指定规格
        run(args[1] if len(args) > 1 else "device", args[0])
    else:
        run("device", SAMPLES["device_hid_keyboard"])
        run("config", SAMPLES["config_hid_keyboard"])
        run("device", SAMPLES["device_bad_type"])
        print("\n提示：解析自己的帧  →  .venv/bin/python examples/run_usb.py <hex> [device|config]")
