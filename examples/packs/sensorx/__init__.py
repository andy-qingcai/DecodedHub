"""sensorx 协议包：只做注册（导入侧效应），零解析逻辑。

导入本包 = 规格与钩子全部就位；规格引用代码只靠名字（"sensorx.sum_xor"）。
"""
import json
from pathlib import Path

from decodehub.decode.fields import register_check_fn, register_fields

from . import checks

register_check_fn("sensorx.sum_xor", checks.sum_xor)
register_fields("sensorx.frame",
                json.loads((Path(__file__).parent / "spec_frame.json").read_text()))
