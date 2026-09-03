#!/usr/bin/env python
"""全链路演示（不经 MCP，直接调领域层）：合成 → 归一化文件 → 嗅探 → 图解码 → 解码 → 表格 + 图表。

运行: .venv/bin/python examples/demo.py
产出: out/demo_uart/（时序图 + events.md/json）与 out/demo_i2c/
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from decodehub.acquisition import load_capture                      # noqa: E402
from decodehub.app import services                                  # noqa: E402
from decodehub.app.session import SessionState                      # noqa: E402
from decodehub.decode.synth import encode_i2c, encode_uart, save_kingst_csv  # noqa: E402
from decodehub.render.format import events_markdown                 # noqa: E402


def demo_uart(state: SessionState, out: Path) -> None:
    print("== UART 演示 ==")
    payload = b"Hello, decodehub!\n"
    wave = encode_uart(payload, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1)
    csv = out / "demo_uart_capture.csv"
    save_kingst_csv(wave, csv)

    # 嗅探 + 摄取（与 MCP lock_source 相同的应用层用例）
    print(services.ingest(state, str(csv), None, None))
    print(services.describe_capture(state))
    services.lock_protocol(state, "uart", {"baud": "auto"}, source="demo_uart_capture")
    services.run_decode(state, {}, source="demo_uart_capture")
    report = state.reports["demo_uart_capture|uart"]
    values = [e.value for e in report.events if e.kind == "uart.frame" and not e.errors]
    ok = bytes(values) == payload
    print(f"解码 {len(values)} 帧，往返 {'✅ 一致' if ok else '❌ 不一致'}: {bytes(values)!r}")

    png, table = services.render_timing(state, None, None, 60, 150, source="demo_uart_capture")
    services.export_events(state, "json", None, source="demo_uart_capture")
    services.export_events(state, "md", None, source="demo_uart_capture")
    print(f"时序图: {png}\n")
    print(table[:800])


def demo_i2c(state: SessionState, out: Path) -> None:
    print("\n== I2C 演示（写寄存器 + 重复起始读出）==")
    wave = encode_i2c([
        {"addr": 0x51, "read": False, "data": [0x00, 0x2A], "repeat_next": True},
        {"addr": 0x51, "read": True, "data": [0x2A], "final_nack": True},
    ], freq=400e3)
    csv = out / "demo_i2c_capture.csv"
    save_kingst_csv(wave, csv)
    print(services.ingest(state, str(csv), None, None))
    alias = state.project.entries[-1].alias
    services.lock_protocol(state, "i2c", {}, source=alias)
    services.run_decode(state, {}, source=alias)
    report = state.reports[alias + "|i2c"]
    for ev in report.events:
        if ev.kind in ("i2c.transfer",):
            print(f"  传输: {ev.label}  errors={ev.errors or '无'}")
    png, _ = services.render_timing(state, None, None, 60, 150, source=alias)
    print(f"时序图: {png}")


def main() -> None:
    out = Path(tempfile.mkdtemp(prefix="decodehub_demo_"))
    state = SessionState()
    demo_uart(state, out)
    demo_i2c(state, out)
    print("\n== 制品清单 ==")
    print(state.artifacts.manifest_markdown())
    print(f"\n演示采集文件: {out}")


if __name__ == "__main__":
    main()
