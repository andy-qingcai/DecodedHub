#!/usr/bin/env python
"""合成 examples/complex-project 的演示采集文件（4 个生产者、多总线）。

用法: python make_captures.py [输出目录=当前目录/captures]
产出:
  captures/la_rev_a.csv / la_rev_b.csv   Kingst LA 8 通道数字 CSV
                                         （SPI + I2C + UART1 + UART2，时间错开）
  captures/scope_ul.npz                  示波器 CH1：上行 DSSS（mho98_npz 键 t_s/v_V）
  captures/scope_dl.npz                  示波器 CH2：下行 DBPSK（与 CH1 同触发 t0）
  captures/mcu_uart_a.csv / mcu_uart_b.csv  MCU ADC 记录的模拟 UART（12bit 原始码值）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from decodehub.decode.synth import (  # noqa: E402
    analogify, encode_downlink, encode_i2c, encode_spi, encode_uart,
)
from decodehub.shared.waves import DigitalWave  # noqa: E402


def _merge_multi(parts: list[tuple[DigitalWave, float, dict[str, str]]],
                 names: list[str], t_end: float) -> DigitalWave:
    """多个数字波 → 一张多通道波。parts: (波, 时间偏移, {子波通道名 → 合并名})。

    各总线时间错开，无需同刻容差归并；UART 通道初始 idle 电平从子波 initial 取。
    """
    bit_of = {n: i for i, n in enumerate(names)}
    initial = 0
    events = []
    for w, off, ren in parts:
        for ch in w.channels:
            b = bit_of[ren[ch]]
            initial |= ((w.initial >> w.bit_index(ch)) & 1) << b
            t_arr, lv_arr = w.edge_stream(ch)
            events += [(float(t) + off, b, int(lv)) for t, lv in zip(t_arr, lv_arr)]
    events.sort(key=lambda x: x[0])
    snap, segs = initial, []
    for t, b, lv in events:
        snap = (snap & ~(1 << b)) | (lv << b)
        segs.append((t, snap))
    return DigitalWave.from_segments(tuple(names), initial, segs, t_end=t_end)


def _merge_multi(parts: list[tuple[DigitalWave, float, dict[str, str]]],
                 names: list[str], t_end: float) -> DigitalWave:
    """parts: (波, 时间偏移, {子波通道名 → 合并波通道名})。"""
    bit_of = {n: i for i, n in enumerate(names)}
    initial = 0
    events = []
    for w, off, ren in parts:
        for ch in w.channels:
            out_name = ren[ch]
            b = bit_of[out_name]
            initial |= ((w.initial >> w.bit_index(ch)) & 1) << b
            t_arr, lv_arr = w.edge_stream(ch)
            events += [(float(t) + off, b, int(lv)) for t, lv in zip(t_arr, lv_arr)]
    events.sort(key=lambda x: x[0])
    snap, segs = initial, []
    for t, b, lv in events:
        snap = (snap & ~(1 << b)) | (lv << b)
        segs.append((t, snap))
    return DigitalWave.from_segments(tuple(names), initial, segs, t_end=t_end)


def make_la(out: Path) -> None:
    """LA 数字采集：SPI(4ch) + I2C(2ch) + UART1(1ch) + UART2(1ch)，时间错开。"""
    spi = encode_spi([0xA5, 0x3C, 0x7F], freq=1e6, cpol=0, cpha=0, word_bits=8)
    i2c = encode_i2c([{"addr": 0x51, "read": False, "data": [0x00, 0x2A], "repeat_next": True},
                      {"addr": 0x51, "read": True, "data": [0x2A], "final_nack": True}],
                     freq=400e3)
    for rev, p1 in (("a", b"Hello LA rev A"), ("b", b"Hello LA rev B")):
        uart1 = encode_uart(p1, baud=115200, idle_bits=2.0, jitter_ui=0.05, seed=1)
        uart2 = encode_uart(b"SENSOR 42", baud=9600, idle_bits=2.0, seed=2)
        parts = [
            (spi, 0.0, {"CLK": "SPI_CLK", "MOSI": "SPI_MOSI", "CS": "SPI_CS"}),
            (i2c, 3.0e-3, {"SCL": "I2C_SCL", "SDA": "I2C_SDA"}),
            (uart1, 6.0e-3, {"TX": "UART1_TX"}),
            (uart2, 12.0e-3, {"TX": "UART2_TX"}),
        ]
        names = ["SPI_CLK", "SPI_MOSI", "SPI_CS", "I2C_SCL", "I2C_SDA",
                 "UART1_TX", "UART2_TX"]
        wave = _merge_multi(parts, names, t_end=25.0e-3)
        save_kingst(wave, out / f"la_rev_{rev}.csv")
        print(f"la_rev_{rev}.csv: {len(names)} 通道, {wave.t_end:.3g}s")


def save_kingst(wave: DigitalWave, path: Path) -> None:
    """Kingst CSV（跳变表；与 decodehub.decode.synth.save_kingst_csv 同格式）。"""
    chs = list(wave.channels)
    bits = [wave.channels.index(c) for c in chs]
    with open(path, "w", encoding="utf-8") as f:
        f.write("Time[s], " + ", ".join(chs) + "\n")
        f.write(f"{wave.t_start:.9f}, "
                + ", ".join(str((wave.initial >> b) & 1) for b in bits) + "\n")
        for t, snap in zip(wave.edges_t, wave.edges_levels):
            f.write(f"{t:.9f}, " + ", ".join(str((int(snap) >> b) & 1) for b in bits) + "\n")


def make_scope(out: Path) -> None:
    """示波器双通道（同触发 t0）：CH1 上行 DSSS + CH2 下行 DBPSK → mho98_npz。"""
    import random

    from decodehub.decode.synth import encode_downlink, encode_uplink

    period, sym = 1.0 / 60.0, 31 * 1e-6
    rng = random.Random(2026)
    frames = [tuple(rng.randrange(2) for _ in range(5)) for _ in range(4)]
    ul = encode_uplink([(0, 1, 0, 1, 0)] + frames, fs=10e6, period_s=period,
                       env_amp=0.5, seed=7)
    anchors = [0.37e-6 + (f + 1) * period + 0.5 * sym for f in range(-1, 4)]
    truth = []
    for _ in anchors:
        slots = [tuple(rng.randrange(2) for _ in range(16)) for _ in range(5)]
        slots.append((0,) * 16)
        truth.append(slots)
    dl = encode_downlink(anchors, truth, fs=10e6, delta_s=850e-6, seed=8)
    for ch, name in ((ul, "scope_ul.npz"), (dl, "scope_dl.npz")):
        t = ch.t0 + np.arange(ch.n) * ch.dt
        np.savez(out / name, t_s=t.astype(np.float64), v_V=ch.samples.astype(np.float32))
        print(f"{name}: {ch.n} 点 @ {1 / ch.dt:.3g}Hz")


def make_mcu(out: Path) -> None:
    """MCU ADC 记录的模拟 UART：12bit 原始码值 CSV（两份，供批量）。"""
    for tag, payload in (("a", b"ADC UART a"), ("b", b"ADC UART b")):
        wave = encode_uart(payload, baud=115200, idle_bits=2.0, jitter_ui=0.08,
                           seed=3 + ord(tag))
        ch = analogify(wave, name=wave.channels[0], fs=500_000.0, noise_sigma=0.02)
        raw = np.clip(np.round(ch.samples / 3.3 * 4095), 0, 4095).astype(int)
        t_ms = (ch.t0 + np.arange(ch.n) * ch.dt) * 1000.0
        with open(out / f"mcu_uart_{tag}.csv", "w", encoding="utf-8") as f:
            f.write("time_ms,adc_raw\n")
            f.writelines(f"{t:.6f},{r}\n" for t, r in zip(t_ms, raw))
        print(f"mcu_uart_{tag}.csv: {ch.n} 点")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "captures")
    out.mkdir(parents=True, exist_ok=True)
    make_la(out)
    make_scope(out)
    make_mcu(out)
    print(f"采集已生成 → {out}")


if __name__ == "__main__":
    main()
