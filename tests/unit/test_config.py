"""decodehub.toml 解析 / 校验 / 采集集展开（ADR-014）。"""

from __future__ import annotations

import pytest

from decodehub.app.config import (
    expand_captures,
    check_capture_coverage,
    find_config,
    load_config,
)
from decodehub.shared.errors import ConfigError


def _write_project(tmp_path, toml: str, captures: dict[str, bytes] | None = None,
                   profiles: dict[str, str] | None = None):
    (tmp_path / "decodehub.toml").write_text(toml, encoding="utf-8")
    cap = tmp_path / "captures"
    cap.mkdir(exist_ok=True)
    for name, data in (captures or {}).items():
        (cap / name).write_bytes(data)
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    for name, data in (profiles or {}).items():
        (prof / f"{name}.json").write_text(data, encoding="utf-8")
    return tmp_path / "decodehub.toml"


_FULL_TOML = """
version = 1

[project]
name = "proj"
profiles_dir = "profiles"
out_dir = "reports"

[runs.main]
profile = "gizmo"

[runs.main.captures]
la = "captures/*.csv"
scope = "captures/ref.csv"

[runs.main.export]
formats = ["csv", "md"]

[runs.main.render]
timing = true
t_min = 0.0
t_max = 0.001
"""

_GIZMO = """
{
  "name": "gizmo",
  "sources": [{"alias": "la", "format": "kingst_csv"},
               {"alias": "scope", "format": "generic_csv"}],
  "locks": [{"source": "la", "protocol": "uart", "params": {}}]
}
"""


class TestLoadConfig:
    def test_full_config(self, tmp_path):
        path = _write_project(tmp_path, _FULL_TOML, profiles={"gizmo": _GIZMO})
        cfg = load_config(path)
        assert cfg.version == 1
        assert cfg.name == "proj"
        assert cfg.profiles_dir == tmp_path / "profiles"
        assert cfg.out_dir == tmp_path / "reports"
        run = cfg.resolve_run("main")
        assert run.profile_name == "gizmo"
        assert run.captures == {"la": "captures/*.csv", "scope": "captures/ref.csv"}
        assert run.export.formats == ["csv", "md"]
        assert run.render.timing is True
        assert run.render.t_max == 0.001

    def test_defaults_and_single_run_resolution(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.only]
profile = "p"
[runs.only.captures]
la = "x.csv"
""")
        cfg = load_config(path)
        assert cfg.profiles_dir == tmp_path / "profiles"
        assert cfg.out_dir == tmp_path / "reports"
        assert cfg.resolve_run(None).name == "only"

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="不存在"):
            load_config(tmp_path / "nope.toml")

    def test_find_config_cwd(self, tmp_path, monkeypatch):
        _write_project(tmp_path, "[runs.a]\nprofile='p'\n[runs.a.captures]\nx='y'")
        monkeypatch.chdir(tmp_path)
        assert find_config(None) == tmp_path / "decodehub.toml"
        monkeypatch.chdir(tmp_path / "captures")
        with pytest.raises(ConfigError, match="decodehub.toml"):
            find_config(None)

    def test_bad_toml_syntax(self, tmp_path):
        path = _write_project(tmp_path, "version = = 1")
        with pytest.raises(ConfigError, match="TOML 语法错误"):
            load_config(path)

    def test_version_mismatch(self, tmp_path):
        path = _write_project(tmp_path, "version = 2\n[runs.a]\nprofile='p'")
        with pytest.raises(ConfigError, match="不支持的配置版本 2"):
            load_config(path)

    def test_unknown_top_key(self, tmp_path):
        path = _write_project(tmp_path, "version = 1\nrunz = 1")
        with pytest.raises(ConfigError, match="未知字段 'runz'"):
            load_config(path)

    def test_no_runs(self, tmp_path):
        path = _write_project(tmp_path, "version = 1\n[project]\nname='x'")
        with pytest.raises(ConfigError, match=r"\[runs"):
            load_config(path)

    def test_profile_and_decode_exclusive(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.a]
profile = "p"
[runs.a.decode.sources.la]
format = "kingst_csv"
[runs.a.captures]
la = "x.csv"
""")
        with pytest.raises(ConfigError, match="二选一"):
            load_config(path)

    def test_neither_profile_nor_decode(self, tmp_path):
        path = _write_project(tmp_path, "[runs.a]\n[runs.a.captures]\nla='x.csv'")
        with pytest.raises(ConfigError, match="profile"):
            load_config(path)

    def test_missing_captures(self, tmp_path):
        path = _write_project(tmp_path, "[runs.a]\nprofile='p'")
        with pytest.raises(ConfigError, match="captures"):
            load_config(path)

    def test_unknown_export_format(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.a]
profile = 'p'
[runs.a.captures]
la = 'x.csv'
[runs.a.export]
formats = ["xlsx"]
""")
        with pytest.raises(ConfigError, match="未知导出格式"):
            load_config(path)

    def test_unknown_run_key(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.a]
profile = 'p'
caputures = { la = 'x.csv' }
""")
        with pytest.raises(ConfigError, match="未知字段 'caputures'"):
            load_config(path)

    def test_resolve_run_unknown(self, tmp_path):
        path = _write_project(tmp_path, _FULL_TOML, profiles={"gizmo": _GIZMO})
        cfg = load_config(path)
        with pytest.raises(ConfigError, match="不存在.*main"):
            cfg.resolve_run("nope")


class TestInlineDecode:
    def test_inline_spec(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.inline]
[runs.inline.decode.sources.la]
format = "kingst_csv"
[runs.inline.decode.locks.la]
protocol = "uart"
params = { baud = 115200 }
[runs.inline.captures]
la = "captures/x.csv"
""")
        cfg = load_config(path)
        spec = cfg.resolve_profile(cfg.resolve_run("inline"))
        assert spec.name == "inline"
        assert [s.alias for s in spec.sources] == ["la"]
        assert spec.locks[0].protocol == "uart"
        assert spec.locks[0].params == {"baud": 115200}

    def test_inline_bad_lock(self, tmp_path):
        path = _write_project(tmp_path, """
[runs.inline]
[runs.inline.decode.sources.la]
format = "kingst_csv"
[runs.inline.decode.locks.la]
protocal = "uart"
[runs.inline.captures]
la = "x.csv"
""")
        with pytest.raises(ConfigError, match="未知字段 'protocal'"):
            load_config(path)


class TestExpandCaptures:
    def _cfg(self, tmp_path, toml=_FULL_TOML):
        path = _write_project(tmp_path, toml, profiles={"gizmo": _GIZMO})
        return load_config(path)

    def test_batch_sorted_and_broadcast(self, tmp_path):
        caps = {f"fw_{i}.csv": b"x" for i in [3, 1, 2]}
        _write_project(tmp_path, _FULL_TOML, captures=caps, profiles={"gizmo": _GIZMO})
        ref = tmp_path / "captures" / "scope" / "ref.csv"  # 子目录不被 *.csv 命中
        ref.parent.mkdir()
        ref.write_bytes(b"y")
        cfg = load_config(tmp_path / "decodehub.toml")
        run = cfg.resolve_run("main")
        sets = expand_captures(cfg, run, overrides={"scope": "captures/scope/ref.csv"})
        assert len(sets) == 3
        assert [s.label for s in sets] == ["001_fw_1", "002_fw_2", "003_fw_3"]
        assert all(s.files["scope"] == ref for s in sets)
        assert sets[0].files["la"] == tmp_path / "captures" / "fw_1.csv"

    def test_single_set_label_is_stem(self, tmp_path):
        toml = """
[runs.s]
profile = 'gizmo'
[runs.s.captures]
la = 'captures/only.csv'
scope = 'captures/only.csv'
"""
        _write_project(tmp_path, toml, captures={"only.csv": b"x"},
                       profiles={"gizmo": _GIZMO})
        cfg = self._cfg(tmp_path, toml)
        sets = expand_captures(cfg, cfg.resolve_run("s"))
        assert len(sets) == 1 and sets[0].label == "only"

    def test_empty_glob(self, tmp_path):
        _write_project(tmp_path, _FULL_TOML, profiles={"gizmo": _GIZMO})
        cfg = load_config(tmp_path / "decodehub.toml")
        with pytest.raises(ConfigError, match="未命中"):
            expand_captures(cfg, cfg.resolve_run("main"))

    def test_missing_literal_file(self, tmp_path):
        _write_project(tmp_path, _FULL_TOML, profiles={"gizmo": _GIZMO})
        (tmp_path / "captures" / "a.csv").write_bytes(b"x")  # ref.csv 缺失
        cfg = load_config(tmp_path / "decodehub.toml")
        with pytest.raises(ConfigError, match="ref.csv"):
            expand_captures(cfg, cfg.resolve_run("main"))

    def test_count_mismatch_rejected(self, tmp_path):
        toml = """
[runs.m]
profile = 'gizmo'
[runs.m.captures]
la = 'captures/a*.csv'
scope = 'captures/b*.csv'
"""
        _write_project(tmp_path, toml,
                       captures={"a1.csv": b"", "a2.csv": b"", "a3.csv": b"",
                                 "b1.csv": b"", "b2.csv": b""},
                       profiles={"gizmo": _GIZMO})
        cfg = load_config(tmp_path / "decodehub.toml")
        with pytest.raises(ConfigError, match="数量不一致"):
            expand_captures(cfg, cfg.resolve_run("m"))

    def test_overrides_replace_patterns(self, tmp_path):
        _write_project(tmp_path, _FULL_TOML, captures={"fw_1.csv": b"x"},
                       profiles={"gizmo": _GIZMO})
        cfg = load_config(tmp_path / "decodehub.toml")
        run = cfg.resolve_run("main")
        sets = expand_captures(cfg, run, overrides={
            "la": str(tmp_path / "captures" / "fw_1.csv"),
            "scope": str(tmp_path / "captures" / "fw_1.csv"),
        })
        assert len(sets) == 1 and sets[0].label == "fw_1"

    def test_absolute_glob_rejected(self, tmp_path):
        _write_project(tmp_path, _FULL_TOML, captures={"a.csv": b""},
                       profiles={"gizmo": _GIZMO})
        cfg = load_config(tmp_path / "decodehub.toml")
        with pytest.raises(ConfigError, match="绝对路径不支持通配符"):
            expand_captures(cfg, cfg.resolve_run("main"),
                            overrides={"la": "/tmp/*.csv"})

    def test_coverage(self, tmp_path):
        _write_project(tmp_path, _FULL_TOML, profiles={"gizmo": _GIZMO})
        cfg = load_config(tmp_path / "decodehub.toml")
        run = cfg.resolve_run("main")
        spec = cfg.resolve_profile(run)
        check_capture_coverage(cfg, run, spec, None)  # 全覆盖，不抛

        with pytest.raises(ConfigError, match="别名.*'extra'.*未在源定义中"):
            check_capture_coverage(cfg, run, spec, overrides={"extra": "x.csv"})

        # 只绑定 la 的运行 → 缺 scope
        d2 = tmp_path / "min"
        d2.mkdir()
        (d2 / "decodehub.toml").write_text(
            "[runs.min]\nprofile = 'gizmo'\n[runs.min.captures]\nla = 'a.csv'",
            encoding="utf-8")
        (d2 / "profiles").mkdir()
        (d2 / "profiles" / "gizmo.json").write_text(_GIZMO, encoding="utf-8")
        cfg2 = load_config(d2 / "decodehub.toml")
        run2 = cfg2.resolve_run("min")
        with pytest.raises(ConfigError, match="scope.*--capture"):
            check_capture_coverage(cfg2, run2, cfg2.resolve_profile(run2), None)
