"""锁实例命名（ADR-023）：同源同协议多路并存；重锁显式报错；报告按实例名区分。"""

from __future__ import annotations

import json

import pytest

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.synth import encode_uart, save_kingst_csv
from decodehub.shared.errors import ProtocolLockError

PAYLOAD = b"HI"


@pytest.fixture()
def st(tmp_path):
    csv = tmp_path / "cap.csv"
    save_kingst_csv(encode_uart(PAYLOAD, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1),
                    csv)
    s = SessionState()
    services.ingest(s, str(csv), None, None)
    return s


def test_two_uart_locks_coexist_on_one_source(st):
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, None, name="uart1")
    services.lock_protocol(st, "uart", {"baud": 9600, "rx": "TX"}, None, name="uart2")
    assert sorted(st.locks) == ["cap|uart1", "cap|uart2"]
    services.run_decode(st, None, None)
    assert {k for k in st.reports} == {"cap|uart1", "cap|uart2"}
    # 实例名即报告 protocol；115200 那路解出原文，9600 那路是另一"设备"的结果
    r1 = st.reports["cap|uart1"]
    assert r1.protocol == "uart1"
    vals = bytes(e.value for e in r1.events if e.kind == "uart.frame" and not e.errors)
    assert vals == PAYLOAD


def test_default_name_backward_compatible(st):
    services.lock_protocol(st, "uart", {"baud": 115200}, None)
    assert "cap|uart" in st.locks
    assert st.locks["cap|uart"].name == "uart"  # 缺省物化为协议名


def test_duplicate_lock_key_rejected(st):
    services.lock_protocol(st, "uart", {"baud": 115200}, None)
    with pytest.raises(ProtocolLockError, match="绝不静默覆盖"):
        services.lock_protocol(st, "uart", {"baud": 9600}, None)
    with pytest.raises(ProtocolLockError, match="绝不静默覆盖"):
        services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart1")
        services.lock_protocol(st, "uart", {"baud": 9600}, None, name="uart1")


def test_overrides_rebuild_on_named_lock(st):
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, None, name="uart1")
    services.run_decode(st, {"stop_bits": 2}, "cap|uart1")  # 重建路径带 name
    assert "cap|uart1" in st.locks
    assert next(iter(st.reports.values())).params["stop_bits"] == 2


def test_export_and_render_by_instance_name(st, tmp_path):
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, None, name="uart1")
    services.lock_protocol(st, "uart", {"baud": 9600, "rx": "TX"}, None, name="uart2")
    services.run_decode(st, None, None)
    p = services.export_events(st, "md", str(tmp_path / "e.md"), None, protocol="uart2")
    assert p.is_file()
    png, _table = services.render_timing(st, None, None, 60, 150, None, protocol="uart1")
    assert str(png).endswith(".png")


def test_unlock_by_instance_name(st):
    services.lock_protocol(st, "uart", {"baud": 115200}, None, name="uart1")
    services.lock_protocol(st, "uart", {"baud": 9600}, None, name="uart2")
    msg = services.unlock_protocol(st, None, "uart1")
    assert "uart1" in msg
    assert sorted(st.locks) == ["cap|uart2"]


def test_pipeline_tap_by_instance_name(st):
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, None, name="uart1")
    services.bind_pipeline(st, "frames", "cap|uart1",
                           [{"type": "event_filter", "kinds": ["uart.frame"]}])
    services.run_decode(st, None, None)
    assert "cap|frames" in st.reports


def test_profile_roundtrip_keeps_names(st, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, None, name="uart1")
    services.lock_protocol(st, "uart", {"baud": 9600, "rx": "TX"}, None, name="uart2")
    services.save_profile(st, "multi-uart", "")
    data = json.loads((tmp_path / "profiles" / "multi-uart.json").read_text(encoding="utf-8"))
    assert {l["name"] for l in data["locks"]} == {"uart1", "uart2"}

    st2 = SessionState()
    services.open_project(st2, "multi-uart", {"cap": str(tmp_path / "cap.csv")})
    assert sorted(st2.locks) == ["cap|uart1", "cap|uart2"]


def test_config_array_locks_form(tmp_path):
    from decodehub.app.config import load_config
    (tmp_path / "decodehub.toml").write_text("""
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "uart1"
params = { baud = 115200, rx = "TX" }
[[runs.main.decode.locks]]
source = "la"
protocol = "uart"
name = "uart2"
params = { baud = 9600, rx = "TX" }
[runs.main.captures]
la = "x.csv"
""", encoding="utf-8")
    cfg = load_config(tmp_path / "decodehub.toml")
    spec = cfg.resolve_profile(cfg.resolve_run("main"))
    assert [(l.source, l.name) for l in spec.locks] == [("la", "uart1"), ("la", "uart2")]
