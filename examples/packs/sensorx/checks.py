"""sensorx 协议的自定义校验/变换——纯函数，代码唯一住处（受信侧）。

引擎预设（CRC 目录）覆盖不了的算法写在这里，经 register_check_fn /
register_field_fn 以名字暴露给规格；随代码版本走、走评审。
"""


def sum_xor(data: bytes) -> int:
    """示例校验和：字节求和后与 0x5A 异或（返回值须落在字段位宽内）。"""
    return (sum(data) ^ 0x5A) & 0xFF
