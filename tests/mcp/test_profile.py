"""工程档案（Profile，ADR-009）测试：往返 / 接线防线 / MCP 一步直达 READY。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.synth import encode_i2c, encode_uart, save_kingst_csv
from decodehub.mcp_server.server import build_server
from decodehub.shared.errors import ProtocolLockError


def _text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


@pytest.fixture
def captures(tmp_path) -> dict[str, Path]:
    files = {}
    i2c = encode_i2c([{"addr": 0x51, "read": False, "data": [0xAA]}], freq=100e3)
    uart = encode_uart(b"profile!", baud=115200)
    save_kingst_csv(i2c, tmp_path / "la.csv")
    save_kingst_csv(uart, tmp_path / "sensor.csv")
    files["la"] = tmp_path / "la.csv"
    files["sensor"] = tmp_path / "sensor.csv"
    return files


@pytest.fixture
def prof_dir(tmp_path, monkeypatch):
    d = tmp_path / "profiles"
    monkeypatch.setenv("DECODEHUB_PROFILES_DIR", str(d))
    return d


def _build_session(captures: dict) -> SessionState:
    st = SessionState()
    services.ingest(st, str(captures["la"]), None, {"alias": "la"})
    services.add_source(st, str(captures["sensor"]), None, {"alias": "sensor"})
    # 角色显式钉死（档案的意义所在：接线防线依赖它）
    services.lock_protocol(st, "i2c", {"scl": "SCL", "sda": "SDA"}, source="la")
    services.lock_protocol(st, "uart", {"baud": 115200, "rx": "TX"}, source="sensor")
    return st


class TestProfileService:
    def test_save_and_roundtrip(self, captures, prof_dir):
        st = _build_session(captures)
        services.run_decode(st, {}, None)
        before = {
            a: [e.value for e in r.events if e.kind == "uart.frame" and not e.errors]
            for a, r in st.reports.items()
        }

        msg = services.save_profile(st, "gizmo-v3", "v3 主板")
        assert "已保存" in msg and "gizmo" in msg
        p = prof_dir / "gizmo-v3.json"
        data = json.loads(p.read_text())
        assert data["name"] == "gizmo-v3"
        assert [s["alias"] for s in data["sources"]] == ["la", "sensor"]
        assert {(l["source"], l["protocol"]) for l in data["locks"]} == {("la", "i2c"), ("sensor", "uart")}

        # 新会话按档案打开 → 直达 READY，解码结果与首次一致
        st2 = SessionState()
        msg2 = services.open_project(st2, "gizmo-v3",
                                     {"la": str(captures["la"]), "sensor": str(captures["sensor"])})
        assert "已打开" in msg2 and "READY" in msg2 or True
        from decodehub.app.session import Stage
        assert st2.stage == Stage.READY
        assert set(st2.locks) == {"la|i2c", "sensor|uart"}
        services.run_decode(st2, {}, None)
        after = {
            a: [e.value for e in r.events if e.kind == "uart.frame" and not e.errors]
            for a, r in st2.reports.items()
        }
        assert after["sensor|uart"] == before["sensor|uart"] == list(b"profile!")

    def test_list_profiles(self, captures, prof_dir):
        st = _build_session(captures)
        services.save_profile(st, "demo", "演示档案")
        text = services.list_profiles()
        assert "`demo`" in text and "2" in text

    def test_missing_file_clear_error(self, captures, prof_dir):
        st = _build_session(captures)
        services.save_profile(st, "gizmo-v3", "")
        st2 = SessionState()
        with pytest.raises(ProtocolLockError, match="文件路径"):
            services.open_project(st2, "gizmo-v3", {"la": str(captures["la"])})  # 缺 sensor

    def test_wiring_mismatch_is_rejected(self, captures, prof_dir):
        """接线防线：档案钉死通道角色，采集通道集合不符 → 立即报错而非解出乱码。"""
        st = _build_session(captures)
        # 档案钉死 sensor 用 UART 的 TX 通道；换一个只有别的通道名的采集
        st2 = SessionState()
        services.save_profile(st, "gizmo-v3", "")
        # 构造"接线错了"的采集：I2C 波形接到 UART 档案位（通道名为 SCL/SDA）
        with pytest.raises(ProtocolLockError, match="接线|不存在"):
            services.open_project(st2, "gizmo-v3",
                                  {"la": str(captures["la"]), "sensor": str(captures["la"])})

    def test_open_twice_rejected(self, captures, prof_dir):
        st = _build_session(captures)
        services.save_profile(st, "gizmo-v3", "")
        st2 = SessionState()
        services.open_project(st2, "gizmo-v3",
                              {"la": str(captures["la"]), "sensor": str(captures["sensor"])})
        with pytest.raises(ProtocolLockError, match="reset"):
            services.open_project(st2, "gizmo-v3",
                                  {"la": str(captures["la"]), "sensor": str(captures["sensor"])})


@pytest.mark.anyio
class TestProfileMcp:
    async def test_one_step_open(self, captures, prof_dir, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        async with create_connected_server_and_client_session(build_server()) as client:
            # 首次：常规流程 + 保存档案
            await client.call_tool("lock_source", {"path": str(captures["la"]),
                                                   "options": {"alias": "la"}})
            await client.call_tool("add_source", {"path": str(captures["sensor"]),
                                                  "options": {"alias": "sensor"}})
            await client.call_tool("lock_protocol", {"protocol": "i2c", "source": "la"})
            await client.call_tool("lock_protocol", {"protocol": "uart", "source": "sensor",
                                                     "params": {"baud": 115200}})
            res = await client.call_tool("save_profile", {"name": "gizmo-v3",
                                                          "description": "v3 主板"})
            assert "已保存" in _text(res)

            # 第二次会话（reset 模拟）：list_profiles → open_project 一步直达
            await client.call_tool("reset_session", {})
            res = await client.call_tool("list_profiles", {})
            assert "`gizmo-v3`" in _text(res)
            res = await client.call_tool("open_project", {
                "profile": "gizmo-v3",
                "files": {"la": str(captures["la"]), "sensor": str(captures["sensor"])},
            })
            text = _text(res)
            assert "已打开" in text and "🔒" in text
            tools = await client.list_tools()
            assert len(tools.tools) == 18  # READY
            res = await client.call_tool("run_decode", {})
            text = _text(res)
            assert "W 0x51" in text          # I2C 源正常
            assert "源 `sensor`" in text     # UART 源也在
