# render/ — C4 呈现上下文

- `format.py`：事件 → Markdown 表 / JSON / CSV（协议专有列查 decode/presentation.py 注册表）。
- `plots.py`：数字时序图 / 模拟叠加图（matplotlib Agg；画哪些协议族查注册表 `plot_family`）。
- `artifacts.py`：制品登记与 `out/<capture_id>/` 约定。

**新增协议在此改 0 处**——去 `decode/protocols/<p>/present.py` 注册呈现约定（ADR-013），
本目录只依赖 `DecodedEvent` 基础字段。规范见 `docs/42-render.md`。
