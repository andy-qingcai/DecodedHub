"""产物命名模板与路径模板（ADR-024）：文件名/目录全部可配，缺省 = 原行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decodehub.app.config import load_config
from decodehub.cli.main import main
from decodehub.shared.errors import ConfigError
from decodehub.decode.synth import encode_uart, save_kingst_csv

PAYLOAD = b"NAMING"


@pytest.fixture()
def project(tmp_path):
    csv = tmp_path / "captures" / "cap.csv"
    csv.parent.mkdir()
    save_kingst_csv(encode_uart(PAYLOAD, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1),
                    csv)
    (tmp_path / "decodehub.toml").write_text("""
version = 1
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
[runs.main.render]
timing = true
""", encoding="utf-8")
    return tmp_path


class TestConfigParsing:
    def test_naming_parsed(self, tmp_path):
        (tmp_path / "decodehub.toml").write_text("""
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
[runs.main.captures]
la = "x.csv"
[runs.main.naming]
decoded = "{run}-{label}-decoded.json"
events = "exp-{source}-{protocol}.{ext}"
index = "INDEX.md"
""", encoding="utf-8")
        run = load_config(tmp_path / "decodehub.toml").resolve_run("main")
        assert run.naming.decoded == "{run}-{label}-decoded.json"
        assert run.naming.events == "exp-{source}-{protocol}.{ext}"
        assert run.naming.timing == ""  # 未配置 = 缺省行为

    def test_unknown_placeholder_rejected(self, tmp_path):
        (tmp_path / "decodehub.toml").write_text("""
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
[runs.main.captures]
la = "x.csv"
[runs.main.naming]
events = "{bogus}.md"
""", encoding="utf-8")
        with pytest.raises(ConfigError, match="未知占位符.*bogus"):
            load_config(tmp_path / "decodehub.toml")

    def test_unknown_naming_key_rejected(self, tmp_path):
        (tmp_path / "decodehub.toml").write_text("""
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
[runs.main.captures]
la = "x.csv"
[runs.main.naming]
decooded = "x.json"
""", encoding="utf-8")
        with pytest.raises(ConfigError, match="未知字段 'decooded'"):
            load_config(tmp_path / "decodehub.toml")

    def test_set_dir_placeholder_restricted(self, tmp_path):
        (tmp_path / "decodehub.toml").write_text("""
[runs.main]
set_dir = "{bogus}/{label}"
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
[runs.main.captures]
la = "x.csv"
""", encoding="utf-8")
        with pytest.raises(ConfigError, match="set_dir.*bogus"):
            load_config(tmp_path / "decodehub.toml")


class TestRunOutput:
    def test_custom_naming_and_paths(self, project):
        toml = project / "decodehub.toml"
        s = toml.read_text(encoding="utf-8")
        s = s.replace("[runs.main]\n",
                      '[runs.main]\nout_dir = "custom_reports"\nset_dir = "sets/{label}"\n', 1)
        s += ("\n[runs.main.naming]\n"
              'decoded = "{run}-{label}-result.json"\n'
              'events = "exp-{source}-{protocol}.{ext}"\n'
              'timing = "pic_{source}.png"\n'
              'index = "INDEX.md"\n'
              'summary = "sum.json"\n')
        toml.write_text(s, encoding="utf-8")

        assert main(["run", str(toml)]) == 0
        set_dir = project / "custom_reports" / "main" / "sets" / "cap"
        assert (set_dir / "main-cap-result.json").is_file()
        assert (set_dir / "exp-la-uart.md").is_file()
        assert (set_dir / "pic_la.png").is_file()
        assert not (set_dir / "decoded.json").exists()
        assert (project / "custom_reports" / "main" / "INDEX.md").is_file()
        assert (project / "custom_reports" / "main" / "sum.json").is_file()

        doc = json.loads((set_dir / "main-cap-result.json").read_text(encoding="utf-8"))
        vals = [e["value"] for r in doc["reports"] for e in r["events"]
                if e["kind"] == "uart.frame" and not e["errors"]]
        assert bytes(vals) == PAYLOAD

    def test_index_summary_run_placeholder(self, project):
        """index/summary 模板的 {run} 占位符替换成运行名（而非字面花括号）。"""
        s = (project / "decodehub.toml").read_text(encoding="utf-8")
        s += ("\n[runs.main.naming]\n"
              'index = "INDEX_{run}.md"\n'
              'summary = "SUMMARY_{run}.json"\n')
        (project / "decodehub.toml").write_text(s, encoding="utf-8")

        assert main(["run", str(project / "decodehub.toml")]) == 0
        base = project / "reports" / "main"
        assert (base / "INDEX_main.md").is_file()
        assert (base / "SUMMARY_main.json").is_file()
        assert not (base / "INDEX_{run}.md").exists()
        summary = json.loads((base / "SUMMARY_main.json").read_text(encoding="utf-8"))
        assert summary["run"] == "main"

    def test_set_dir_subdirectory_nesting(self, project):
        s = (project / "decodehub.toml").read_text(encoding="utf-8").replace(
            "[runs.main]\n", '[runs.main]\nset_dir = "by_day/{label}/v1"\n', 1)
        (project / "decodehub.toml").write_text(s, encoding="utf-8")
        assert main(["run", str(project / "decodehub.toml")]) == 0
        assert (project / "reports" / "main" / "by_day" / "cap" / "v1" / "decoded.json").is_file()

    def test_cli_out_still_wins(self, project):
        assert main(["run", str(project / "decodehub.toml"),
                     "--out", str(project / "elsewhere")]) == 0
        assert (project / "elsewhere" / "main" / "cap" / "decoded.json").is_file()
