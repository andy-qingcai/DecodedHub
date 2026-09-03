"""CLI/headless 全链路集成测试（ADR-014）：合成采集 → decodehub.toml → run/validate/diff。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decodehub.cli.main import main

PAYLOAD_A = b"HELLO FW A"
PAYLOAD_B = b"HELLO FW B"


def _make_captures(cap_dir: Path, files: dict[str, bytes]) -> None:
    from decodehub.decode.synth import encode_uart, save_kingst_csv

    cap_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        wave = encode_uart(payload, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1)
        save_kingst_csv(wave, cap_dir / f"{name}.csv")


_PROFILE = json.dumps({
    "name": "uart-proj",
    "sources": [{"alias": "la", "format": "kingst_csv"}],
    "locks": [{"source": "la", "protocol": "uart", "params": {"baud": 115200}}],
})

_TOML = """
version = 1

[project]
name = "cli-proj"
out_dir = "reports"

[runs.main]
profile = "uart-proj"

[runs.main.captures]
la = "captures/*.csv"

[runs.main.export]
formats = ["csv", "md"]

[runs.main.render]
timing = true
"""


@pytest.fixture()
def project(tmp_path):
    _make_captures(tmp_path / "captures", {"fw_a": PAYLOAD_A, "fw_b": PAYLOAD_B})
    (tmp_path / "decodehub.toml").write_text(_TOML, encoding="utf-8")
    prof = tmp_path / "profiles"
    prof.mkdir()
    (prof / "uart-proj.json").write_text(_PROFILE, encoding="utf-8")
    return tmp_path


def _payload_of(decoded_path: Path) -> bytes:
    doc = json.loads(decoded_path.read_text(encoding="utf-8"))
    vals = [e["value"] for r in doc["reports"] for e in r["events"]
            if e["kind"] == "uart.frame" and not e["errors"]]
    return bytes(vals)


class TestRun:
    def test_batch_run(self, project):
        assert main(["run", str(project / "decodehub.toml")]) == 0
        run_dir = project / "reports" / "main"
        labels = sorted(d.name for d in run_dir.iterdir() if d.is_dir())
        assert labels == ["001_fw_a", "002_fw_b"]
        for label, payload in zip(labels, [PAYLOAD_A, PAYLOAD_B]):
            d = run_dir / label
            assert (d / "decoded.json").is_file()
            assert (d / "events.csv").is_file()
            assert (d / "events.md").is_file()
            assert (d / "timing_1.png").is_file()
            assert _payload_of(d / "decoded.json") == payload
        index = (run_dir / "index.md").read_text(encoding="utf-8")
        assert "2/2 成功" in index
        assert "`001_fw_a`" in index
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["profile"] == "uart-proj"
        assert all(o["ok"] for o in summary["outcomes"])

    def test_no_render_no_export(self, project):
        toml = """
[runs.bare]
profile = "uart-proj"
[runs.bare.captures]
la = "captures/fw_a.csv"
"""
        (project / "bare.toml").write_text(toml, encoding="utf-8")
        assert main(["run", str(project / "bare.toml")]) == 0
        d = project / "reports" / "bare" / "fw_a"
        assert (d / "decoded.json").is_file()
        assert not (d / "events.csv").exists()
        assert not (d / "timing_1.png").exists()

    def test_capture_override(self, project):
        cfg = str(project / "decodehub.toml")
        cap = str(project / "captures" / "fw_b.csv")
        assert main(["run", cfg, "--capture", f"la={cap}"]) == 0
        assert (project / "reports" / "main" / "fw_b" / "decoded.json").is_file()
        assert not (project / "reports" / "main" / "001_fw_a").exists()

    def test_preflight_missing_file(self, project, capsys):
        """字面路径缺失在展开阶段即报错（不进入任何采集集）。"""
        toml = """
[runs.bad]
profile = "uart-proj"
[runs.bad.captures]
la = "captures/missing.csv"
"""
        (project / "bad.toml").write_text(toml, encoding="utf-8")
        assert main(["run", str(project / "bad.toml")]) == 1
        assert "未命中" in capsys.readouterr().err
        assert not (project / "reports" / "bad" / "index.md").exists()

    def test_per_set_failure_recorded_in_index(self, project):
        """接线/参数与档案不符 → 该采集集失败，但批处理继续并记录进索引。"""
        prof = json.loads(_PROFILE)
        prof["name"] = "bad-rx"
        prof["locks"][0]["params"] = {"baud": 115200, "rx": "NOPE"}  # 通道不存在
        (project / "profiles" / "bad-rx.json").write_text(json.dumps(prof),
                                                          encoding="utf-8")
        toml = """
[runs.drift]
profile = "bad-rx"
[runs.drift.captures]
la = "captures/*.csv"
"""
        (project / "drift.toml").write_text(toml, encoding="utf-8")
        assert main(["run", str(project / "drift.toml")]) == 1
        index = (project / "reports" / "drift" / "index.md").read_text(encoding="utf-8")
        assert "❌" in index and "0/2 成功" in index
        summary = json.loads((project / "reports" / "drift" / "summary.json")
                             .read_text(encoding="utf-8"))
        assert all("NOPE" in (o["error"] or "") for o in summary["outcomes"])

    def test_inline_decode_run(self, project):
        toml = """
[runs.inline]
[runs.inline.decode.sources.la]
format = "kingst_csv"
[runs.inline.decode.locks.la]
protocol = "uart"
params = { baud = 115200 }
[runs.inline.captures]
la = "captures/fw_a.csv"
"""
        (project / "inline.toml").write_text(toml, encoding="utf-8")
        assert main(["run", str(project / "inline.toml")]) == 0
        assert (_payload_of(project / "reports" / "inline" / "fw_a" / "decoded.json")
                == PAYLOAD_A)

    def test_preflight_empty_glob(self, project, capsys):
        """glob 未命中任何文件 → 预检报错。"""
        toml = """
[runs.drift]
profile = "uart-proj"
[runs.drift.captures]
la = "captures2/*.csv"
"""
        (project / "drift.toml").write_text(toml, encoding="utf-8")
        assert main(["run", str(project / "drift.toml")]) == 1
        assert "未命中" in capsys.readouterr().err


class TestValidate:
    def test_valid(self, project, capsys):
        assert main(["validate", str(project / "decodehub.toml")]) == 0
        assert "批量" in capsys.readouterr().out

    def test_bad_protocol_typo(self, project, capsys):
        prof = json.loads(_PROFILE)
        prof["locks"][0]["protocol"] = "uarts"
        (project / "profiles" / "uart-proj.json").write_text(json.dumps(prof),
                                                             encoding="utf-8")
        assert main(["validate", str(project / "decodehub.toml")]) == 1
        assert "未知协议 'uarts'" in capsys.readouterr().out

    def test_missing_profile(self, project):
        (project / "profiles" / "uart-proj.json").unlink()
        assert main(["validate", str(project / "decodehub.toml")]) == 1


class TestDiff:
    def test_same_and_different(self, project):
        assert main(["run", str(project / "decodehub.toml")]) == 0
        a = str(project / "reports" / "main" / "001_fw_a" / "decoded.json")
        b = str(project / "reports" / "main" / "002_fw_b" / "decoded.json")
        out = str(project / "diff.md")
        assert main(["diff", a, a, "--out", out]) == 0
        assert "完全一致" in Path(out).read_text(encoding="utf-8")
        assert main(["diff", a, b, "--out", out]) == 1
        text = Path(out).read_text(encoding="utf-8")
        assert "uart.frame" in text and "0x42" in text

    def test_missing_file_error(self, project):
        assert main(["diff", "/nope/a.json", "/nope/b.json"]) == 1
