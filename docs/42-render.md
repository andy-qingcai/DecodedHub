# 42 · 呈现上下文（Render）

> 状态：已定案 · C4 · 输入契约：`DecodeReport` / `DigitalWave` / `AnalogChannel`（只读）

## 职责

把解码结果与波形变成 LLM 可直接消费的形态：Markdown 表、PNG 图表、JSON/CSV 导出文件；并登记制品。**不在图引擎内**（图保持纯函数，写文件副作用集中于本上下文，见 ADR-002）。

## v1 图表清单

| 图 | 内容 | 触发条件 |
|---|---|---|
| **数字时序图** `render_timing` | 多通道方波（跳变驱动 step 绘制）+ 解码事件 span 着色（`axvspan`，按 `ann_class` 配色）+ 帧编号标注 | READY 后任意调用；数字源或切片后均可 |
| **模拟叠加图** `render_analog` | 原始模拟波形 + 阈值虚线 + 切片出的数字轨迹（下方泳道）| 采集含模拟通道时 |

延后（v1.5+）：帧级甘特图（帧数 > ~50 时自动）、总线统计图。

## 实现规范（matplotlib，Agg）

- `matplotlib.use("Agg")` 于 pyplot 导入前；`plt.close(fig)` 收尾。
- 数字轨迹：`ax.step(t, y, where="post")`，O(跳变数)；通道间用实/虚/点线交替增强灰度可辨性。
- 事件 span：`axvspan(t0, t1, alpha=0.15, color=ann_class→色)`；标注策略 = **图内只标编号**（`1 2 3`），详细内容进同一次返回的 Markdown 表（图文配对）；窄于 ~40px 的 span 不标注、尾部聚合为 "…N more"。
- 时间轴：`EngFormatter(unit="s")` 自动 µs/ms 缩放。
- 中文字形安全：`font.sans-serif = ["PingFang SC", "Hiragino Sans GB", "DejaVu Sans"]` + `axes.unicode_minus=False`（Agg 支持逐字形回退，中英混排无豆腐块）。
- 尺寸：`figsize=(10, 1.8+0.8×n_channels)`，`dpi=150`（≈1500px 宽）；PNG 目标 ≤ 600 KB（base64×1.33 后仍在客户端 ~1 MB 限制内），超限自动降 dpi=120 再分段。
- 模拟长采集：每像素列 min/max 包络抽取（2 点/像素），防混叠与文件膨胀。

## 输出与制品约定

- 目录：`out/<capture_id>/`；文件名确定性：`timing_<n>.png`、`analog_<ch>_<n>.png`、`events.json|csv|md`。重复渲染幂等覆盖（文件名无时间戳）。
- `Artifact` 登记：路径、种类（figure/table/export）、覆盖的时间窗/帧数、像素/字节数。
- MCP 返回：`[ImageContent(png₁), …, TextContent(制品清单 + Markdown 表)]`——图内联给客户端渲染，路径留给用户复盘/缩放。

## 文本格式

**Markdown 事件表**（`render_timing` / `export_events(md)` 共用生成器）：

```
| # | t_start | Δt | 类型 | 内容 | 状态 |
|---|---------|-----|------|------|------|
| 1 | 1.250 ms | 104 µs | i2c.transfer | W 0x51 [0x12 0x34] ACK,ACK | OK |
```

- 时间自动单位（同 EngFormatter 逻辑）；`#` 列与时序图 span 编号一一对应。
- 协议专有列（地址/方向/ACK、MOSI/MISO 等）**已实现**为 per-kind 注册表（ADR-013）：
  `decode/decodehub/decode/presentation.py`（源码 `src/decodehub/decode/presentation.py`），
  各协议在 `protocols/<p>/present.py` 注册中文名/内容列/CSV 专有列；render 只查表，
  未注册 kind 走 fallback（类型=原文、内容=label）。错误状态列用 `ERR:parity` 形式；
  payload > 8 字节折行（`…` 前缀）。

**JSON**：`DecodeReport` → `{"protocol", "params", "counts", "events": [event.to_dict(), …]}`（事件含全部专有字段）。

**CSV**：公共列 `idx,t_start,t_end,duration_s,kind,label,ann_class,errors` + 协议专有列（同一注册表按注册顺序展开并集：`value_or_address,read,data_bytes,acks,mosi,miso,word_bits,pream_ok,confidence,fc_hz,slot,frame`；行内只填所属协议注册的列）。
