"""锁键一致性（Bug 1/2/2b/6）：同键锁与管线名冲突、`|` 实例名、声明期查重。

三道防线共用 app/session.py 的锁键助手：
- 声明期（load_config / load_profile / decodehub validate）：同键锁、
  管线名与锁实例名冲突、含 `|` 的名字 → ConfigError；
- 运行期（lock_protocol / bind_pipeline）：同键再锁/绑定 → ProtocolLockError，
  绝不静默覆盖（ADR-023）。
"""

from __future__ import annotations

import json

import pytest

from decodehub.app import services
from decodehub.app.config import load_config
from decodehub.app.profile import (LockSpec, ProfileSpec, SourceSpec,
                                    load_profile, save_profile,
                                    validate_profile_dict)
from decodehub.app.session import SessionState
from decodehub.cli.main import main as cli_main
from decodehub.decode.synth import encode_uart, save_kingst_csv
from decodehub.shared.errors import ConfigError, ProtocolLockError

PAYLOAD = b"OK"
CHAIN = [{"type": "event_filter", "kinds": ["uart.frame"]}]


@pytest.fixture()
def st(tmp_path):
    csv = tmp_path / "cap.csv"
    save_kingst_csv(encode_uart(PAYLOAD, baud=115200, idle_bits=2.0, jitter_ui=0.05,
                                seed=1), csv)
    s = SessionState()
    services.ingest(s, str(csv), None, None)
    return s


def _write_toml(tmp_path, body: str) -> str:
    p = tmp_path / "decodehub.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


# ------------------------------------------------- Bug 1：同键锁声明期查重 ---

class TestDeclarationDuplicateLockKeys:
    def test_toml_array_two_same_key_locks_rejected(self, tmp_path):
        """[[locks]] 数组写两把同源同名锁 → load_config 报 ConfigError，不放行。"""
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "uart1"
params = { baud = 115200 }
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "uart1"
params = { baud = 9600 }
[runs.main.captures]
la = "x.csv"
""")
        with pytest.raises(ConfigError, match="重复"):
            load_config(toml)

    def test_toml_array_default_name_collision_rejected(self, tmp_path):
        """缺省名 = 协议名：同源两把同名协议锁同样同键，声明期拒绝。"""
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
[runs.main.captures]
la = "x.csv"
""")
        with pytest.raises(ConfigError) as ei:
            load_config(toml)
        assert "重复" in str(ei.value) and "la|uart" in str(ei.value)

    def test_profile_same_key_rejected(self, tmp_path):
        """档案 JSON 两把同键锁 → validate_profile_dict 报问题，load_profile 报
        ConfigError（声明期，不等 open_project/run）。"""
        spec = ProfileSpec(
            name="dup-locks",
            sources=[SourceSpec(alias="la")],
            locks=[LockSpec(source="la", protocol="uart"),
                   LockSpec(source="la", protocol="uart", params={"baud": 9600})])
        save_profile(spec, dir=tmp_path)
        problems = validate_profile_dict(spec.to_dict())
        assert any("重复" in p for p in problems)
        with pytest.raises(ConfigError, match="重复"):
            load_profile("dup-locks", dir=tmp_path)

    def test_profile_pipe_char_in_name_rejected(self, tmp_path):
        spec = ProfileSpec(
            name="pipe-name",
            sources=[SourceSpec(alias="la")],
            locks=[LockSpec(source="la", protocol="uart", name="a|b")])
        save_profile(spec, dir=tmp_path)
        problems = validate_profile_dict(spec.to_dict())
        assert any("不能包含" in p for p in problems)
        with pytest.raises(ConfigError, match="不能包含"):
            load_profile("pipe-name", dir=tmp_path)


# --------------------------------------- Bug 2/2b：bind_pipeline 键冲突检查 ---

class TestBindPipelineKeyConflict:
    def test_pipeline_named_as_lock_instance_rejected_lock_intact(self, st):
        """管线名 = 锁实例名（如 uart2）→ 报错，且原锁与其报告完好无损。"""
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart2")
        with pytest.raises(ProtocolLockError, match="已被协议锁"):
            services.bind_pipeline(st, "uart2", "cap|uart2", CHAIN)
        assert st.locks["cap|uart2"].protocol == "uart"  # 原锁未被管线替换
        services.run_decode(st, None, None)
        assert set(st.reports) == {"cap|uart2"}
        vals = bytes(e.value for e in st.reports["cap|uart2"].events
                     if e.kind == "uart.frame" and not e.errors)
        assert vals == PAYLOAD

    def test_two_named_locks_keep_two_reports(self, st):
        """回归场景：uart1+uart2 两把锁 + 管线名撞 uart2 → 修复前报告 2→1，现在报错且仍是 2 份。"""
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart1")
        services.lock_protocol(st, "uart", {"baud": 9600}, None, name="uart2")
        with pytest.raises(ProtocolLockError, match="已被协议锁"):
            services.bind_pipeline(st, "uart2", "cap|uart2", CHAIN)
        services.run_decode(st, None, None)
        assert set(st.reports) == {"cap|uart1", "cap|uart2"}

    def test_pipeline_pipeline_same_name_rejected(self, st):
        """Bug 2b：管线与管线同源同名 → 拒绝，第一条管线不受影响。"""
        services.lock_protocol(st, "uart", {"baud": 115200}, None)
        services.bind_pipeline(st, "frames", None, CHAIN)
        with pytest.raises(ProtocolLockError, match="已被"):
            services.bind_pipeline(st, "frames", "uart", CHAIN)
        assert "cap|frames" in st.locks
        assert st.locks["cap|frames"].params.get("_pipeline") is True

    def test_lock_protocol_same_key_second_rejected(self, st):
        """运行期同键再锁保持既有报错语义（绝不静默覆盖）。"""
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart1")
        with pytest.raises(ProtocolLockError, match="绝不静默覆盖"):
            services.lock_protocol(st, "uart", {"baud": 9600}, None, name="uart1")
        assert [l.name for l in st.locks.values()] == ["uart1"]


# ------------------------------------------------- Bug 6：`|` 名字入口拒绝 ---

class TestPipeCharRejected:
    def test_lock_protocol_rejects_pipe_in_name(self, st):
        with pytest.raises(ProtocolLockError, match="不能包含"):
            services.lock_protocol(st, "uart", {"baud": 115200}, None, name="a|b")
        assert not st.locks

    def test_bind_pipeline_rejects_pipe_in_name(self, st):
        with pytest.raises(ProtocolLockError, match="不能包含"):
            services.bind_pipeline(st, "p|q", None, CHAIN)

    def test_toml_lock_name_with_pipe_rejected(self, tmp_path):
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "la|a"
[runs.main.captures]
la = "x.csv"
""")
        with pytest.raises(ConfigError, match="不能包含"):
            load_config(toml)

    def test_toml_pipeline_name_with_pipe_rejected(self, tmp_path):
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
[runs.main.captures]
la = "x.csv"
[runs.main.pipelines."p|q"]
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""")
        with pytest.raises(ConfigError, match="不能包含"):
            load_config(toml)


# ------------------------------- Bug 2 声明期：管线名与锁实例名同名即拒绝 ---

class TestDeclarationPipelineLockConflict:
    def test_toml_inline_pipeline_named_as_lock(self, tmp_path):
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "uart2"
[runs.main.captures]
la = "x.csv"
[runs.main.pipelines.uart2]
tap = "la|uart2"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""")
        with pytest.raises(ConfigError, match="同名"):
            load_config(toml)

    def test_validate_flags_profile_pipeline_conflict(self, tmp_path, capsys):
        """档案引用的锁在 decodehub validate 声明期即报（config 加载无锁可查）。"""
        save_profile(ProfileSpec(
            name="pc", sources=[SourceSpec(alias="la")],
            locks=[LockSpec(source="la", protocol="uart")]), dir=tmp_path / "profiles")
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
profile = "pc"
[runs.main.captures]
la = "cap.csv"
[runs.main.pipelines.uart]
tap = "la|uart"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""")
        assert cli_main(["validate", toml]) == 1
        assert "同名" in capsys.readouterr().out

    def test_run_preflight_flags_profile_pipeline_conflict(self, tmp_path, capsys):
        """run 也在开跑前预检报 ConfigError，不等逐采集集 bind 失败。"""
        save_profile(ProfileSpec(
            name="pc2", sources=[SourceSpec(alias="la")],
            locks=[LockSpec(source="la", protocol="uart")]), dir=tmp_path / "profiles")
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
profile = "pc2"
[runs.main.captures]
la = "cap.csv"
[runs.main.pipelines.uart]
tap = "la|uart"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""")
        assert cli_main(["run", toml]) == 1
        assert "同名" in capsys.readouterr().err


# --------------------------------------------------------- 合法名字不受影响 ---

class TestLegalNamesUnaffected:
    def test_chinese_space_hyphen_lock_name(self, st):
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="串口-1 上行")
        assert "cap|串口-1 上行" in st.locks
        services.run_decode(st, None, None)
        assert "cap|串口-1 上行" in st.reports

    def test_chinese_hyphen_pipeline_name(self, st):
        services.lock_protocol(st, "uart", {"baud": 115200}, None)
        services.bind_pipeline(st, "帧过滤-1", None, CHAIN)
        services.run_decode(st, None, None)
        assert "cap|帧过滤-1" in st.reports

    def test_legal_names_pass_declaration(self, tmp_path):
        toml = _write_toml(tmp_path, """
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "串口-1 主线"
[runs.main.captures]
la = "x.csv"
[runs.main.pipelines."帧-提取 v2"]
tap = "la|串口-1 主线"
chain = [{ type = "event_filter", kinds = ["uart.frame"] }]
""")
        cfg = load_config(toml)  # 不抛
        spec = cfg.resolve_profile(cfg.resolve_run("main"))
        assert spec.locks[0].name == "串口-1 主线"

    def test_legal_profile_names_no_problems(self):
        spec = ProfileSpec(
            name="legal", sources=[SourceSpec(alias="la")],
            locks=[LockSpec(source="la", protocol="uart", name="串口 1"),
                   LockSpec(source="la", protocol="uart", name="uart-2")])
        assert validate_profile_dict(spec.to_dict()) == []

    def test_named_locks_still_coexist_and_export(self, st, tmp_path):
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart1")
        services.lock_protocol(st, "uart", {"baud": 9600}, None, name="uart2")
        services.run_decode(st, None, None)
        p = services.export_events(st, "json", str(tmp_path / "e.json"), None,
                                   protocol="uart2")
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["protocol"] == "uart2"
