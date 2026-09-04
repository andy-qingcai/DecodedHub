"""CLI 集成：decodehub.toml 声明的管线（ADR-020）——一次运行，多 sink 分开导出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decodehub.cli.main import main

PAYLOAD = b"HELLO PIPELINE"


@pytest.fixture()
def project(tmp_path):
    from decodehub.decode.synth import encode_uart, save_kingst_csv

    csv = tmp_path / "captures" / "cap.csv"
    csv.parent.mkdir()
    save_kingst_csv(encode_uart(PAYLOAD, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1),
                    csv)
    (tmp_path / "decodehub.toml").write_text("""
version = 1

[project]
name = "pipe-proj"

[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
params = { baud = 115200 }
[runs.main.captures]
la = "captures/cap.csv"

[runs.main.export]
formats = ["md"]

# 扁写行内形式（推荐）：整条链一眼看完
[runs.main.pipelines.frames]
tap = "uart"
chain = [
    { type = "event_filter", kinds = ["uart.frame"] },
]
""", encoding="utf-8")
    return tmp_path


def test_run_produces_independent_sinks(project):
    assert main(["run", str(project / "decodehub.toml")]) == 0
    d = project / "reports" / "main" / "cap"
    doc = json.loads((d / "decoded.json").read_text(encoding="utf-8"))
    keys = {(r["source"], r["protocol"]) for r in doc["reports"]}
    assert ("la", "uart") in keys and ("la", "frames") in keys

    # 同一管线过滤后的事件数 = 上游 uart.frame 数；分开成两个导出文件
    by_proto = {(r["source"], r["protocol"]): r for r in doc["reports"]}
    frames = by_proto[("la", "frames")]
    uart_frames = [e for e in by_proto[("la", "uart")]["events"]
                   if e["kind"] == "uart.frame"]
    assert frames["total"] == len(uart_frames) > 0

    assert (d / "events-la-uart.md").is_file()
    frames_md = (d / "events-la-frames.md").read_text(encoding="utf-8")
    assert "（无事件）" not in frames_md and "| 1 |" in frames_md  # 呈现层类型列是中文
    assert not (d / "events.md").exists()  # 多报告时不用无后缀名

    index = (project / "reports" / "main" / "index.md").read_text(encoding="utf-8")
    assert "uart.frame×28" in index  # 双 sink 聚合：上游 14 帧 + 管线 14 帧


def test_validate_reports_pipelines(project, capsys):
    assert main(["validate", str(project / "decodehub.toml")]) == 0
    assert "管线 1" in capsys.readouterr().out


def test_pipeline_binding_failure_recorded(project):
    (project / "decodehub.toml").write_text(
        (project / "decodehub.toml").read_text(encoding="utf-8")
        .replace('tap = "uart"', 'tap = "nope"'),
        encoding="utf-8")
    assert main(["run", str(project / "decodehub.toml")]) == 1
    index = (project / "reports" / "main" / "index.md").read_text(encoding="utf-8")
    assert "❌" in index


def test_legacy_array_of_tables_form_still_works(project):
    """嵌套 [[...chain]] 旧写法语义不变（文档推荐扁写）。"""
    (project / "legacy.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
params = { baud = 115200 }
[runs.main.captures]
la = "captures/cap.csv"
[runs.main.pipelines.frames]
tap = "uart"
[[runs.main.pipelines.frames.chain]]
type = "event_filter"
[runs.main.pipelines.frames.chain.params]
kinds = ["uart.frame"]
""", encoding="utf-8")
    assert main(["run", str(project / "legacy.toml")]) == 0
    doc = json.loads((project / "reports" / "main" / "cap" / "decoded.json")
                     .read_text(encoding="utf-8"))
    assert ("la", "frames") in {(r["source"], r["protocol"]) for r in doc["reports"]}
