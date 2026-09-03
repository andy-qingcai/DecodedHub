# ADR-006 · 可视化：matplotlib Agg 静态 PNG + 图文配对；MCP 内联 ImageContent + 路径双返回

- 状态：已接受（2026-09-03）

## 决策

1. **技术**：matplotlib + Agg 后端，服务端静态渲染。不引入 plotly/前端。
2. **v1 图表集**：数字时序图（核心）+ 模拟叠加图（模拟源必备）。帧甘特/统计图延后（v1.5）。
3. **图文配对**：图内帧 span 只标**编号**，详细内容进同一次工具返回的 Markdown 表——规避图内文字重叠，且 LLM 对表格的解析远稳于图内小字。
4. **交付**：`[ImageContent(内联 base64 PNG), …, TextContent(表 + 制品清单)]` 混合返回；PNG 同时落盘 `out/<capture_id>/`（客户端丢图后可复盘）。
5. **默认**：`figsize=(10, 1.8+0.8×n_channels)`、`dpi=150`（≈1500px 宽、线稿 50–300KB）；>600KB 自动降 dpi=120 再分段（base64 ×1.33 后需留客户端 ~1MB 响应限制余量）。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| plotly/HTML 交互图 | MCP 客户端普遍只内联渲染静态图；HTML 需浏览器闭环；体积大 |
| ASCII 时序图 | 1500px 标注图在任何规模下都优于字符画；不值得维护双渲染 |
| 仅返回文件路径 | 多数客户端不自动打开文件，LLM 也看不到图 |
| 仅内联不落盘 | 客户端丢弃图片后无法复盘/缩放 |

## 细节规范

- 数字轨迹 `ax.step(where="post")` O(跳变)；通道线型交替（实/虚/点）保证灰度可辨；
- span 配色按 `ann_class`（start/stop/data/ack/warn/err → tab10 固定映射），`alpha=0.15`；
- 时间轴 `EngFormatter(unit="s")`；中文字形 `PingFang SC → Hiragino Sans GB → DejaVu Sans` 回退链 + `axes.unicode_minus=False`；
- 模拟长采集按像素列 min/max 包络抽取（防混频/膨胀）。

## 后果

- 正面：零前端依赖；LLM 即看即得；制品可追溯。
- 负面：无交互缩放——缓解：`render_timing(t_min, t_max)` 参数化窗口，LLM 可对感兴趣区间二次渲染"放大"。
