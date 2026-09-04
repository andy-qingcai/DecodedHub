"""增量运行（ADR-025）：sink 指纹未变 → 跳过；部分数据更新 → 定向重算；锚依赖传播。

健壮性（部分重算不破坏产物集）：删管线产物 → 上游锁反向传播强制重算；
删单 sink 产物 → 导出以原名恢复（命名依据采集集全部 sink 数，非会话内报告数）；
图序号 {n} 与本次重算子集无关（timing = 全量 sink 序，analog = 源自身计数）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from decodehub.cli.main import main
from decodehub.decode.synth import encode_uart, save_kingst_csv


def _write_mcu(path: Path, payload: bytes) -> None:
    """MCU ADC CSV：模拟 UART → 12bit 原始码值。"""
    from decodehub.decode.synth import analogify

    wave = encode_uart(payload, baud=115200, idle_bits=2.0, jitter_ui=0.08, seed=3)
    ch = analogify(wave, name=wave.channels[0], fs=500_000.0, noise_sigma=0.02)
    raw = np.clip(np.round(ch.samples / 3.3 * 4095), 0, 4095).astype(int)
    t_ms = (ch.t0 + np.arange(ch.n) * ch.dt) * 1000.0
    with open(path, "w", encoding="utf-8") as f:
        f.write("time_ms,adc_raw\n")
        f.writelines(f"{t:.6f},{r}\n" for t, r in zip(t_ms, raw))


@pytest.fixture()
def project(tmp_path):
    csv = tmp_path / "captures" / "la.csv"
    csv.parent.mkdir()
    save_kingst_csv(encode_uart(b"HELLO LA", baud=115200, idle_bits=2.0, seed=1), csv)
    _write_mcu(tmp_path / "captures" / "mcu.csv", b"MCU DATA")
    (tmp_path / "decodehub.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
params = { baud = 115200 }
[runs.main.decode.sources.mcu]
format = "mcu_adc_csv"
[runs.main.decode.sources.mcu.options]
vref = 3.3
bits = 12
[[runs.main.decode.locks]]
source = "mcu"
protocol = "uart"
name = "mcu_uart"
params = { threshold = 1.65, baud = 115200 }
[runs.main.captures]
la = "captures/la.csv"
mcu = "captures/mcu.csv"
[runs.main.export]
formats = ["md"]
[runs.main.pipelines.mcu_frames]
tap = "mcu|mcu_uart"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""", encoding="utf-8")
    return tmp_path


def _manifest(project: Path, label: str = "la") -> dict:
    return json.loads((project / "reports" / "main" / label / "manifest.json")
                      .read_text(encoding="utf-8"))


def _events_md(project: Path) -> Path:
    return project / "reports" / "main" / "la" / "events-mcu-mcu_uart.md"


def _age(path: Path) -> None:
    os.utime(path, (1000000000, 1000000000))  # 显式旧 mtime，规避文件系统时间粒度


def test_second_incremental_run_skips_all(project):
    assert main(["run", str(project / "decodehub.toml")]) == 0
    _age(_events_md(project))
    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0
    sinks = _manifest(project)["sinks"]
    assert {v["status"] for v in sinks.values()} == {"skipped"}
    assert len(sinks) == 3  # la|uart + mcu|mcu_uart + 管线
    assert _events_md(project).stat().st_mtime == 1000000000  # 旧产物原地沿用


def test_partial_data_update_reruns_only_affected(project):
    assert main(["run", str(project / "decodehub.toml")]) == 0
    la_md = project / "reports" / "main" / "la" / "events-la-uart.md"
    _age(la_md)
    _age(_events_md(project))

    _write_mcu(project / "captures" / "mcu.csv", b"NEW DATA")  # 只更新 MCU 数据
    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0

    sinks = _manifest(project)["sinks"]
    assert sinks["la|uart"]["status"] == "skipped"        # 未受影响：沿用
    assert sinks["mcu|mcu_uart"]["status"] == "rerun"     # 数据变了：重算
    assert sinks["mcu|mcu_frames"]["status"] == "rerun"       # 消费者跟着重算
    assert la_md.stat().st_mtime == 1000000000            # la 产物未动
    assert _events_md(project).stat().st_mtime > 1000000000


def test_naming_change_invalidates_all(project):
    assert main(["run", str(project / "decodehub.toml")]) == 0
    toml = project / "decodehub.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[runs.main.naming]\ndecoded = "{run}-{label}-result.json"\n',
                    encoding="utf-8")
    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0
    sinks = _manifest(project)["sinks"]
    assert {v["status"] for v in sinks.values()} == {"rerun"}
    assert (project / "reports" / "main" / "la" / "main-la-result.json").is_file()


def test_full_run_then_incremental_without_manifest_gap(project):
    """全量 run 也写 manifest——之后任何一次增量都能直接命中。"""
    assert main(["run", str(project / "decodehub.toml")]) == 0
    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0
    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0
    assert {v["status"] for v in _manifest(project)["sinks"].values()} == {"skipped"}


def test_deleted_pipeline_artifact_reruns_upstream_and_restores(project):
    """删管线产物 → 管线进 rerun，其 tap 上游锁经**反向传播**强制重算。

    否则上游锁指纹未变被跳过、不 lock_protocol，bind_pipeline 无上游可 tap，
    整个采集集失败、管线报告与产物整体丢失。
    """
    assert main(["run", str(project / "decodehub.toml")]) == 0
    la_dir = project / "reports" / "main" / "la"
    pipe_md = la_dir / "events-mcu-mcu_frames.md"
    la_md = la_dir / "events-la-uart.md"
    _age(la_md)  # 无关 sink（la）的产物应原封不动；mcu 上游被强制重算会刷新，正常
    pipe_md.unlink()

    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0

    sinks = _manifest(project)["sinks"]
    assert sinks["mcu|mcu_frames"]["status"] == "rerun"   # 产物被删：重算
    assert sinks["mcu|mcu_uart"]["status"] == "rerun"     # tap 上游反向传播：强制重算
    assert sinks["la|uart"]["status"] == "skipped"        # 无关 sink 沿用
    assert pipe_md.is_file()                              # 管线报告以原名恢复
    assert la_md.stat().st_mtime == 1000000000


def test_deleted_chain_tail_propagates_to_root_lock(tmp_path):
    """管线链（p2 tap p1 tap 锁）删最末端产物 → 反向传播**递归到根**：全链重算。"""
    csv = tmp_path / "captures" / "la.csv"
    csv.parent.mkdir()
    save_kingst_csv(encode_uart(b"CHAIN", baud=115200, idle_bits=2.0, seed=1), csv)
    (tmp_path / "decodehub.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
params = { baud = 115200 }
[runs.main.captures]
la = "captures/la.csv"
[runs.main.export]
formats = ["md"]
[runs.main.pipelines.p1]
tap = "uart"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
[runs.main.pipelines.p2]
tap = "p1"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""", encoding="utf-8")
    assert main(["run", str(tmp_path / "decodehub.toml")]) == 0
    d = tmp_path / "reports" / "main" / "la"
    (d / "events-la-p2.md").unlink()

    assert main(["run", str(tmp_path / "decodehub.toml"), "--incremental"]) == 0

    sinks = _manifest(tmp_path, "la")["sinks"]
    assert [sinks[k]["status"] for k in ("la|uart", "la|p1", "la|p2")] == ["rerun"] * 3
    assert (d / "events-la-p2.md").is_file()


def test_deleted_lock_events_restored_with_original_name(project):
    """删一把锁的 events 文件 → 增量后以**原名**恢复。

    缺省命名的"单报告"判断依据是采集集全部 sink 数（含跳过的），不是会话内
    报告数——否则只重算一把锁时退化成 events.<ext>，原文件名永不再生成。
    """
    assert main(["run", str(project / "decodehub.toml")]) == 0
    la_dir = project / "reports" / "main" / "la"
    la_md = la_dir / "events-la-uart.md"
    mcu_md, pipe_md = _events_md(project), la_dir / "events-mcu-mcu_frames.md"
    for p in (mcu_md, pipe_md):
        _age(p)
    la_md.unlink()

    assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0

    assert la_md.is_file()                              # 原名恢复（而非 events.md）
    assert not (la_dir / "events.md").exists()          # 不产生退化名
    assert mcu_md.stat().st_mtime == 1000000000         # 其他 sink 产物不动
    assert pipe_md.stat().st_mtime == 1000000000
    sinks = _manifest(project)["sinks"]
    assert sinks["la|uart"]["status"] == "rerun"
    assert {sinks["mcu|mcu_uart"]["status"],
            sinks["mcu|mcu_frames"]["status"]} == {"skipped"}


@pytest.fixture()
def timing_project(tmp_path):
    """双锁 + timing/analog 渲染：锁定 {n} 序号在部分重算时的稳定性。"""
    csv = tmp_path / "captures" / "la.csv"
    csv.parent.mkdir()
    save_kingst_csv(encode_uart(b"HELLO LA", baud=115200, idle_bits=2.0, seed=1), csv)
    _write_mcu(tmp_path / "captures" / "mcu.csv", b"MCU DATA")
    (tmp_path / "decodehub.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
params = { baud = 115200 }
[runs.main.decode.sources.mcu]
format = "mcu_adc_csv"
[runs.main.decode.sources.mcu.options]
vref = 3.3
bits = 12
[[runs.main.decode.locks]]
source = "mcu"
protocol = "uart"
name = "mcu_uart"
params = { threshold = 1.65, baud = 115200 }
[runs.main.captures]
la = "captures/la.csv"
mcu = "captures/mcu.csv"
[runs.main.render]
timing = true
analog = true
""", encoding="utf-8")
    return tmp_path


def test_partial_rerun_figure_numbering_stable(timing_project):
    """{n} 序号与本次重算谁无关（行为锁定）：

    - timing：n = sink 在全量 sink 序中的 1-based 序号——只重算 mcu 时它仍拿
      timing_2.png，不会从 1 重来覆盖 la 的 timing_1.png；
    - analog：缺省名带 source 限定（analog-mcu_1.png），跨源/跨批次永不重名。
    """
    assert main(["run", str(timing_project / "decodehub.toml")]) == 0
    d = timing_project / "reports" / "main" / "la"
    t1, t2, an = d / "timing_1.png", d / "timing_2.png", d / "analog-mcu_1.png"
    assert t1.is_file() and t2.is_file() and an.is_file()
    _age(t1)

    _write_mcu(timing_project / "captures" / "mcu.csv", b"NEW DATA")
    assert main(["run", str(timing_project / "decodehub.toml"), "--incremental"]) == 0

    assert t1.stat().st_mtime == 1000000000        # 跳过 sink 的旧图原封不动
    assert t2.stat().st_mtime > 1000000000         # 重算 sink 以原序号刷新
    assert an.is_file() and an.stat().st_mtime > 1000000000
    sinks = _manifest(timing_project)["sinks"]
    assert sinks["la|uart"]["status"] == "skipped"
    assert sinks["mcu|mcu_uart"]["status"] == "rerun"
    assert "timing_2.png" in sinks["mcu|mcu_uart"]["files"]
    assert "analog-mcu_1.png" in sinks["mcu|mcu_uart"]["files"]


class TestAnchorPropagation:
    """只更新上行数据 → 下行锁（锚定上行）也必须重算。"""

    @pytest.fixture()
    def scope_project(self, tmp_path):
        import random

        from decodehub.decode.synth import encode_downlink, encode_uplink

        def to_npz(ch, path):
            t = ch.t0 + np.arange(ch.n) * ch.dt
            np.savez(path, t_s=t.astype(np.float64), v_V=ch.samples.astype(np.float32))

        period, sym = 1.0 / 60.0, 31 * 1e-6

        def gen(seed: int, out: Path):
            rng = random.Random(seed)
            frames = [tuple(rng.randrange(2) for _ in range(5)) for _ in range(3)]
            ul = encode_uplink([(0, 1, 0, 1, 0)] + frames, fs=10e6, period_s=period, seed=seed)
            anchors = [0.37e-6 + (f + 1) * period + 0.5 * sym for f in range(-1, 3)]
            truth = [[tuple(rng.randrange(2) for _ in range(16)) for _ in range(5)]
                     + [(0,) * 16] for _ in anchors]
            dl = encode_downlink(anchors, truth, fs=10e6, delta_s=850e-6, seed=seed + 1)
            to_npz(ul, out / "captures" / "scope_ul.npz")
            to_npz(dl, out / "captures" / "scope_dl.npz")

        cap = tmp_path / "captures"
        cap.mkdir()
        gen(11, tmp_path)
        (tmp_path / "decodehub.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.scope_ul]
format = "mho98_npz"
[[runs.main.decode.locks]]
source = "scope_ul"
protocol = "uplink"
[runs.main.decode.sources.scope_dl]
format = "mho98_npz"
[[runs.main.decode.locks]]
source = "scope_dl"
protocol = "downlink"
params = { uplink_source = "scope_ul" }
[runs.main.captures]
scope_ul = "captures/scope_ul.npz"
scope_dl = "captures/scope_dl.npz"
""", encoding="utf-8")

        def regen():
            gen(99, tmp_path)  # 不同 seed = 不同数据

        return tmp_path, regen

    def test_uplink_change_reruns_downlink(self, scope_project):
        project, regen = scope_project
        assert main(["run", str(project / "decodehub.toml")]) == 0
        regen()  # 只换上行采集内容
        assert main(["run", str(project / "decodehub.toml"), "--incremental"]) == 0
        sinks = _manifest(project, "scope_ul")["sinks"]
        assert sinks["scope_ul|uplink"]["status"] == "rerun"
        assert sinks["scope_dl|downlink"]["status"] == "rerun"  # 锚依赖传播
