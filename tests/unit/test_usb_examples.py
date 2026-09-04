"""USB 描述符规格样例回归（examples/*.json）：真实协议规格不许悄悄失效。"""

import json
from pathlib import Path

from decodehub.decode.fields import format_field, parse_payload

HERE = Path(__file__).resolve().parent.parent.parent / "examples"


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text())


def _sample(key: str) -> bytes:
    return bytes.fromhex(json.loads((HERE / "usb_samples.json").read_text())[key])


def test_device_descriptor_full_parse():
    fs = parse_payload(_load("usb_device_descriptor.json"), _sample("device_hid_keyboard"))
    by_id = {f.id: f for f in fs}
    assert len(fs) == 14 and all(not f.errors for f in fs)
    assert by_id["bLength"].value == 18
    assert by_id["bDescriptorType"].enum_label == "DEVICE"
    assert by_id["bcdUSB"].value == 0x0200          # USB 2.00（小端）
    assert by_id["bMaxPacketSize0"].enum_label == "64B"
    assert by_id["idVendor"].value == 0x046D        # Logitech VID
    assert by_id["idProduct"].value == 0xC52B
    assert by_id["bNumConfigurations"].value == 1


def test_bad_descriptor_type_marks_valid_error():
    fs = parse_payload(_load("usb_device_descriptor.json"), _sample("device_bad_type"))
    assert "valid" in {f.id: f for f in fs}["bDescriptorType"].errors


def test_configuration_descriptor_nested_switch_and_repeat():
    fs = parse_payload(_load("usb_configuration_descriptor.json"),
                       _sample("config_hid_keyboard"))
    by_id = {f.id: f for f in fs}
    assert all(not f.errors for f in fs)
    assert by_id["wTotalLength"].value == 34        # 9(配置) + 9(接口) + 9(HID) + 7(端点)
    # bmAttributes 0xA0 位拆分：bit7=1（必须）、bit6 自供电=0、bit5 远程唤醒=1
    assert by_id["attr_reserved"].value == 1
    assert by_id["self_powered"].value == 0
    assert by_id["remote_wakeup"].value == 1
    # 呈现提示：长度出十进制、物理单位（value 仍是原始值 50，显示 ×2 = "100 mA"）
    assert format_field(by_id["wTotalLength"]) == "wTotalLength=34"
    assert format_field(by_id["bMaxPower"]) == "bMaxPower=100 mA"
    assert by_id["bMaxPower"].value == 50

    subs = by_id["desc"].children                  # repeat=eos 的嵌套描述符流
    assert [len(s.children) for s in subs] == [3, 3, 3]

    iface = subs[0]
    assert iface.children[1].enum_label == "INTERFACE"
    body = iface.children[2].children
    assert [f.id for f in body[:4]] == ["bInterfaceNumber", "bAlternateSetting",
                                        "bNumEndpoints", "bInterfaceClass"]
    assert body[3].enum_label == "HID" and body[5].enum_label == "KEYBOARD"
    assert body[6].value == 0 and body[7].value == b""   # class_specific 空

    hid = subs[1]                                  # bType 0x21 → 通配 generic_body
    assert hid.children[1].value == 0x21
    assert hid.children[2].children[0].value == b"\x11\x01\x00\x01\x22\x3e\x00"

    ep = subs[2]                                   # 端点地址 0x81 位拆分
    ep_body = ep.children[2].children
    assert ep_body[0].value == 1                   # bit7 = IN
    assert ep_body[2].value == 1                   # bit3..0 = 端点 1
    assert ep_body[3].enum_label == "INTERRUPT"
    assert ep_body[4].value == 8 and ep_body[5].value == 10
