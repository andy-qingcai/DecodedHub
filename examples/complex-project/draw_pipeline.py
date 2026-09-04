"""examples/complex-project 的数据流总览图：4 生产者 → 7 锁 → 10 管线 → 产物。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # 示例目录（仓库根的 examples/complex-project）
sys.path.insert(0, str(ROOT / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Heiti TC",
                                   "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(16.5, 10.5))
ax.set_xlim(0, 100); ax.set_ylim(0, 103); ax.axis("off")

C_SRC, C_LOCK, C_PIPE, C_FS, C_OUT = "#dbe9f6", "#dcf0dc", "#fdeeda", "#ead9f5", "#eeeeee"
E_SRC, E_LOCK, E_PIPE, E_FS, E_OUT = "#2b6cb0", "#2f855a", "#c05621", "#6b46c1", "#555555"

def box(x, y, w, h, text, fc, ec, fs=9.5, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35",
                                fc=fc, ec=ec, lw=1.4))
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", linespacing=1.35)

def arrow(x1, y1, x2, y2, color="#666666", style="-", lw=1.3):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=11, color=color, lw=lw,
                                 linestyle=style, shrinkA=1, shrinkB=1))

def col_header(x, text, color):
    ax.text(x + 8, 100.5, text, ha="center", fontsize=12, fontweight="bold", color=color)

# ---------------- 第 1 列：生产者（采集源） ----------------
CX = 2
col_header(CX, "生产者 · 采集源", "#2b6cb0")
box(CX, 66, 15, 9, "Kingst LA\n7 通道数字 CSV\n(la_*.csv ×2 批量)", C_SRC, E_SRC)
box(CX, 44, 15, 8, "示波器 CH1\n上行 DSSS\n(scope_ul.npz)", C_SRC, E_SRC)
box(CX, 30, 15, 8, "示波器 CH2\n下行 DBPSK\n(scope_dl.npz)", C_SRC, E_SRC)
box(CX, 14, 15, 8, "MCU ADC\n模拟 UART 12bit\n(mcu_*.csv)", C_SRC, E_SRC)

# ---------------- 第 2 列：协议锁（7 把） ----------------
CX = 26
col_header(CX, "协议锁 · 7 把（实例名）", "#2f855a")
lock_x, lock_w, lock_h = CX, 16, 5.6
locks = {
    "la|i2c":    (lock_x, 70.0, "la|i2c\nstretch_warn_s=0.5ms"),
    "la|spi":    (lock_x, 62.0, "la|spi\ncpol/cpha/word_bits…"),
    "la|uart1":  (lock_x, 54.0, "la|uart1 (实例名)\nrx=UART1_TX 115200"),
    "la|uart2":  (lock_x, 46.0, "la|uart2 (实例名)\nrx=UART2_TX 9600"),
    "scope_ul|uplink": (lock_x, 36.0, "scope_ul|uplink\npn_word/chip_s…"),
    "scope_dl|downlink": (lock_x, 26.0, "scope_dl|downlink\n锚定 uplink（扇入）"),
    "mcu|mcu_uart": (lock_x, 16.0, "mcu|mcu_uart\nthreshold=1.65 切片"),
}
for _k, (x, y, t) in locks.items():
    box(x, y, lock_w, lock_h, t, C_LOCK, E_LOCK, fs=8.6)

arrow(17, 70.5, lock_x, 72.8, E_SRC); arrow(17, 69, lock_x, 64.8, E_SRC)
arrow(17, 68, lock_x, 56.8, E_SRC);  arrow(17, 67, lock_x, 48.8, E_SRC)
arrow(17, 48, lock_x, 38.8, E_SRC)
arrow(17, 34, lock_x, 28.8, E_SRC, style="--")   # downlink 本源
arrow(17, 48, lock_x, 40.5, E_SRC, style=":")    # uplink 帧网格锚（跨源）
arrow(17, 18, lock_x, 18.8, E_SRC)

# ---------------- 第 3 列：管线（10 个消费者） ----------------
CX = 52
col_header(CX, "管线 · 10 个消费者（独立报告）", "#c05621")
pipe_x, pipe_w, pipe_h = CX, 17, 4.6
pipes = [
    (92.5, "uart1_frames  滤干净帧",        "la|uart1", E_PIPE, C_PIPE),
    (86.5, "uart1_errors  只看错误",        "la|uart1", E_PIPE, C_PIPE),
    (80.5, "uart1_head  时间窗 t≤7ms",      "la|uart1", E_PIPE, C_PIPE),
    (74.5, "uart1_head_clean 链上链",       "uart1_head", E_FS, C_FS),
    (68.5, "uart2_frames  滤帧",            "la|uart2", E_PIPE, C_PIPE),
    (62.5, "uart2_bytes  field_split 逐字节", "la|uart2", E_FS, C_FS),
    (56.5, "i2c_transfers  只要传输",       "la|i2c", E_PIPE, C_PIPE),
    (50.5, "i2c_fields  field_split reg/val", "la|i2c", E_FS, C_FS),
    (44.5, "uplink_frames  滤帧",           "scope_ul|uplink", E_PIPE, C_PIPE),
    (38.5, "downlink_packets  滤包",        "scope_dl|downlink", E_PIPE, C_PIPE),
]
for y, t, tap, ec, fc in pipes:
    box(pipe_x, y, pipe_w, pipe_h, t, fc, ec, fs=8.6)

# 锁 → 管线
for y_pipe, tap in [(94.8, "la|uart1"), (88.8, "la|uart1"), (82.8, "la|uart1"),
                    (76.8, None), (70.8, "la|uart2"), (64.8, "la|uart2"),
                    (58.8, "la|i2c"), (52.8, "la|i2c"),
                    (46.8, "scope_ul|uplink"), (40.8, "scope_dl|downlink")]:
    if tap is None:  # 链上链：来自 uart1_head 管线
        arrow(pipe_x, 82.8 + 2.3, pipe_x, 76.8 + 2.3, E_FS, lw=1.6)
        continue
    ly = locks[tap][1] + lock_h / 2
    arrow(lock_x + lock_w, ly, pipe_x, y_pipe + 2.3,
          E_PIPE if locks[tap][1] > 42 else "#8a6d1f")

# ---------------- 第 4 列：产物 ----------------
CX = 82
col_header(CX, "产物（每 sink 独立）", "#555555")
box(CX, 62, 16, 26,
    "17 份独立报告 / 采集集\n\n· decoded.json 机器汇总\n· exp-<源>-<协议>.md/csv\n"
    "· pic_<源>_<协议>.png\n· wave_<通道>.png\n\n命名全部模板化 (ADR-024)\n目录 custom_reports/sets/<label>",
    C_OUT, "#888888", fs=9)
box(CX, 40, 16, 12,
    "运行索引\nINDEX.md + sum.json\n（CI 读 summary）", C_OUT, "#888888", fs=9)
box(CX, 20, 16, 13,
    "decodehub diff\nrev A vs rev B\n→ 只有 uart1 及其\n消费者报差异", "#fff5f5", "#c53030", fs=9)

for y in (94.8, 88.8, 82.8, 76.8, 70.8, 64.8, 58.8, 52.8, 46.8, 40.8):
    arrow(pipe_x + pipe_w, y + 2.3, CX, 70, "#999999", lw=0.9)
arrow(CX + 8, 62, CX + 8, 52, "#999999")
arrow(CX + 8, 62, CX + 8, 33, "#999999")

ax.text(50, 0.5, "decodehub.toml · mega 示例（多生产者多消费者）—— 锁键 = 源|实例名 (ADR-023)；"
                 "管线 tap 上游锁的汇 (ADR-020)；紫色 = field_split 报文字段解析 (ADR-016)；"
                 "产物命名模板 (ADR-024)",
        ha="center", fontsize=9, color="#444444")

out = ROOT / "pipeline-diagram.png"
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
print(f"saved → {out}")
