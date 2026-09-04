"""sensorx 参考协议包 roundtrip（docs/43 §8 解耦结构的锁定测试）。"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE / "examples"))

import packs.sensorx  # noqa: F401,E402  导入即注册
from decodehub.decode.fields import (  # noqa: E402
    all_field_specs,
    get_fields,
    parse_payload,
)


def test_pack_registered_on_import():
    assert "sensorx.frame" in all_field_specs()


def test_frame_roundtrip_good():
    fs = parse_payload(get_fields("sensorx.frame"), bytes.fromhex("a50102dead69"))
    by_id = {f.id: f for f in fs}
    assert by_id["cmd"].enum_label == "TEMP"
    assert by_id["len"].value == 2
    assert by_id["payload"].value == b"\xde\xad"
    assert by_id["chk"].value == 0x69
    assert all(not f.errors for f in fs)


def test_frame_bad_checksum_marks_crc():
    # 篡改 chk 一字节：sum_xor(前缀) 不再等于线上值
    fs = parse_payload(get_fields("sensorx.frame"), bytes.fromhex("a50102dead68"))
    assert "crc" in {f.id: f for f in fs}["chk"].errors
    # 其余字段照常可读（错误是数据）
    assert {f.id: f for f in fs}["payload"].value == b"\xde\xad"
