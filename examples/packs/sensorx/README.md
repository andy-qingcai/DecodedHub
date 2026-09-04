# sensorx 示例协议包

演示"多种报文格式 + 自定义校验代码"的推荐解耦结构（docs/43 §8）：

```
帧: sync(1B A5) cmd(1B) len(1B) payload(len B) chk(1B)
chk = (sum(sync..payload) ^ 0x5A) & 0xFF   ← 引擎预设覆盖不了 → checks.py
```

- 声明：`spec_frame.json`（纯数据，可下发）
- 代码：`checks.py`（纯函数，唯一代码住处）
- 绑定：`__init__.py` 只做注册，规格与代码靠名字 `sensorx.sum_xor` 绑定

进程内只导入一次（重复 import 会因重复注册 fail-fast）。
测试：`tests/unit/test_pack_sensorx.py`；试用：

```python
# 需 examples 在 import 路径上：PYTHONPATH=examples（或在 examples 目录内）
import packs.sensorx  # 导入即注册
from decodehub.decode.fields import parse_payload, get_fields
parse_payload(get_fields("sensorx.frame"), bytes.fromhex("a50102dead69"))
```
