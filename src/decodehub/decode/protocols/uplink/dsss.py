"""上行 DSSS 接收机（vendored，算法零改动）。

出处: 原vendored工程（config.py + dsss_rx.py，2026-08 实机调参版）。
协议: 上行 = 每 60Hz 周期一个 ~248µs 突发帧的 DSSS——31-chip m 序列（0x3DA60E45）
扩频、每 bit 一个完整 PN、双极性 NRZ、码片标称 1µs（实测 0.9692µs，接收机自动估计）。
帧 = 前导 001 + 5 数据 bit = 8 符号。

管线: 预条件（抽取到 ~12 样点/chip + 1ms 滑动均值 HPF 剥离 60Hz 包络）→ 全段 FFT
相关 → 梳齿提取符号（锚点+抛物线+LSQ 亚样本精化）→ 双模自适应能量分段 →
突发内帧同步（8 相位 × 2 极性软评分前导）→ 码片速率候选仲裁（前导通过率门限）。
纯噪声/无突发会被诚实拒绝（不输出伪流量）。

按 ADR-010 搬运：DSP 参数在真实信道验证过，不做任何"改进"。
"""

from __future__ import annotations

"""Protocol + strategy hyperparameters (all tunable, profile-based).

Uplink profiles vary: PN word/length, preamble & data lengths, chip rate.
Downlink profiles vary: carrier, cycles/bit, packet length, slot offsets,
frame period. Every detection threshold is relative (MAD/percentile/ratio);
the numeric strategy knobs below are exposed as fields so new profiles can
override them too.
"""

from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class UplinkConfig:
    """Uplink DSSS: NRZ square wave, one full PN per bit.

    Waveform model: chip '1' -> +A for chip_s, chip '0' -> -A (bipolar).
    Frame = pream_bits + n_data_bits data bits -> n_symbols * pn_len * chip_s.
    """

    # ---- protocol shape ----
    pn_word: int = 0x3DA60E45  # 31-chip m-sequence (2^5-1, verified:
    # off-peak autocorr = 1, balance 16/15) - bit31 of the word is 0/dropped
    pn_len: int = 31
    chip_s: float = 1e-6       # 1 chip = 1 us -> Rc = 1 Mchip/s
    msb_first: bool = True     # chip[0] = MSB of pn_word (False: LSB first)
    chip_mapping: str = "bipolar"  # 'bipolar' (1->+A / 0->-A) or 'unipolar'
    pream_bits: tuple = (0, 0, 1)
    n_data_bits: int = 5
    invert_polarity: bool = False

    # ---- strategy hyperparameters (defaults tuned on the live channel) ----
    target_spc: float = 12.0        # decimation target, samples per chip
    env_window_s: float = 1e-3      # moving-average HPF: strips the TX
    #                                 envelope / mains / DC pre-matching
    rate_search_lo: float = 0.85    # chip-rate candidate sweep, x nominal
    rate_search_hi: float = 1.151
    rate_search_step: float = 0.002
    n_rate_candidates: int = 3      # top-k distinct candidates attempted
    rate_gate: float = 0.05         # candidate within +-5% of nominal
    burst_k_mad: float = 10.0       # energy threshold, robust-MAD multiple
    burst_floor_frac: float = 0.15  # threshold floor, fraction of p99
    burst_min_symbols: float = 4.5  # min burst span, in symbols
    burst_gap_symbols: float = 2.0  # in-span gap bridging, in symbols
    cont_thr_frac: float = 0.5      # continuous-mode threshold, x p99
    pream_pass_gate: float = 0.5    # accept decode when preamble passes >=
    pream_warn_gate: float = 0.8    # below this, flag frames in warnings
    span_sweep_frac: float = 0.01   # per-span chip sweep, +-1%
    span_sweep_points: int = 21
    lsq_accept_frac: float = 0.02   # LSQ comb fit acceptance vs span chip0
    window_completeness: float = 0.9  # tooth's PN window inside the surface
    tooth_strong_frac: float = 0.25   # strong-teeth cut (x max / x p95)
    tail_margin_symbols: float = 1.2  # keep teeth past the span end
    seg_margin_symbols: float = 2.5   # span segment margin

    @property
    def pn_bits(self) -> np.ndarray:
        if self.msb_first:
            idx = [self.pn_len - 1 - i for i in range(self.pn_len)]
        else:
            idx = [i for i in range(self.pn_len)]
        # guard: pn_word bit pn_len (and above) must be zero in msb_first
        return np.array([(self.pn_word >> k) & 1 for k in idx], dtype=np.int8)

    @property
    def pn_template(self) -> np.ndarray:
        """Correlation template, one entry per chip."""
        b = self.pn_bits.astype(np.float64)
        if self.chip_mapping == "bipolar":
            t = 2.0 * b - 1.0
        elif self.chip_mapping == "unipolar":
            t = b - b.mean()
        else:
            raise ValueError(f"unknown chip_mapping {self.chip_mapping!r}")
        return -t if self.invert_polarity else t

    @property
    def n_symbols(self) -> int:
        return len(self.pream_bits) + self.n_data_bits

    @property
    def symbol_s(self) -> float:
        return self.pn_len * self.chip_s

    @property
    def frame_s(self) -> float:
        return self.n_symbols * self.symbol_s

    def expected_preamble_signs(self) -> np.ndarray:
        s = 2.0 * np.array(self.pream_bits, dtype=np.float64) - 1.0
        return -s if self.invert_polarity else s


# named protocol profiles: extend here as new PN/frame shapes appear,
# e.g. "pn63": dict(pn_word=..., pn_len=63, pream_bits=(0,0,1,1),
#                   n_data_bits=8, chip_s=0.5e-6)
UPLINK_PROFILES = {
    "default": {},  # 31-chip m-seq, pream 001 + 5 data bits, 1 Mchip/s
}


def uplink_profile(name: str = "default", **overrides) -> UplinkConfig:
    if name not in UPLINK_PROFILES:
        raise KeyError(f"unknown uplink profile {name!r}; "
                       f"have {sorted(UPLINK_PROFILES)}")
    return replace(UplinkConfig(**UPLINK_PROFILES[name]), **overrides)


"""Uplink DSSS receiver for the 60 Hz burst-mode TP uplink.

Channel reality (learned from the real capture): one 256 us uplink frame
burst per 60 Hz period (1.5% duty) riding on a strong low-frequency
envelope, with other ~1 ms-periodic interference bursts. Pipeline:

  0. precondition: decimate to ~12 samples/chip (signal band 0..1 MHz for
     1 Mchip/s NRZ), then remove the sub-kHz envelope with a zero-phase
     1 ms moving-average high-pass (matched filtering on the raw waveform
     locks onto that envelope, so it must go first).
  1. whole-capture FFT correlation against one PN period.
  2. symbol peaks: non-maximum suppression on |corr| (min separation
     0.8 symbol) -> each surviving peak IS one despread symbol: its time
     is the symbol boundary, its (signed) correlation value is the soft
     bit. Self-aligned per symbol, so clock drift needs no tracking loop.
  3. dual-mode adaptive threshold on peak heights (never a fixed level):
     continuous mode (median peak ~= p99) -> 0.5*p99; burst mode ->
     median + 6*MAD of the peak heights.
  4. peak grouping into bursts (inter-peak gap <= 2.5 symbols), then
     frame sync (8 phases x 2 polarities, soft-scored preamble) inside
     each burst; data bits = sign of the peak values.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import correlate, resample_poly




@dataclass
class FrameResult:
    idx: int
    t_start: float
    pream_ok: bool
    data_bits: tuple
    data_hex: str
    mean_conf: float
    burst: int = 0


@dataclass
class UplinkResult:
    frames: list = field(default_factory=list)
    t0: float = 0.0
    symbol_s_est: float = 0.0
    phase: int = 0
    polarity: int = 1
    quality: float = 0.0
    n_bursts: int = 0
    pn_len: int = 31
    z: np.ndarray | None = None
    z_times: np.ndarray | None = None
    warnings: list = field(default_factory=list)

    @property
    def chip_rate_est(self) -> float:
        return 1.0 / (self.symbol_s_est / self.pn_len) if self.symbol_s_est else float("nan")


# ---------------------------------------------------------------- precondition

def _moving_average(y: np.ndarray, win: int) -> np.ndarray:
    """Zero-phase boxcar moving average (reflect-padded)."""
    if win <= 1:
        return np.zeros_like(y)
    half = win // 2
    yp = np.pad(y, half, mode="reflect")
    c = np.concatenate(([0.0], np.cumsum(yp, dtype=np.float64)))
    return (c[win:win + len(y)] - c[:len(y)]) / win


def precondition(y: np.ndarray, fs: float, cfg: UplinkConfig) -> tuple[np.ndarray, float]:
    """Decimate to ~target_spc samples/chip and strip the low-frequency
    envelope (60 Hz TX cycle / mains / DC) with a 1 ms moving-average HPF."""
    spc = fs * cfg.chip_s
    if spc > cfg.target_spc * 2:
        down = max(1, int(round(spc / cfg.target_spc)))
        y2 = resample_poly(y, 1, down)
        fs2 = fs / down
    else:
        y2, fs2 = y, fs
    win = max(1, int(round(fs2 * cfg.env_window_s)))
    y2 = y2 - _moving_average(y2, win)
    return np.asarray(y2, dtype=np.float64), float(fs2)


# ---------------------------------------------------------------- analysis

def _pn_template_rate(cfg: UplinkConfig, fs: float) -> np.ndarray:
    per = max(1, int(round(fs * cfg.chip_s)))
    return np.repeat(cfg.pn_template, per)


def _robust_sigma(x: np.ndarray) -> float:
    med = float(np.median(x))
    return 1.4826 * float(np.median(np.abs(x - med)))


def _corr(y: np.ndarray, cfg: UplinkConfig, fs: float, chip_s: float):
    """Correlate against one PN period built for chip period `chip_s`."""
    per = chip_s * fs
    L = max(cfg.pn_len, int(round(cfg.pn_len * per)))
    tpl = cfg.pn_template[np.floor(np.arange(L) / per).astype(int) % cfg.pn_len]
    return correlate(y, tpl, mode="valid", method="fft")


def chip_candidates(y: np.ndarray, fs: float, cfg: UplinkConfig) -> list:
    """Coarse comb sweep -> top-k distinct chip-period candidates.

    On the real channel the score surface is bimodal: an interference
    structure can outscore the true period on a single-burst window, so
    the final arbiter is the preamble check in decode_uplink, not the
    sweep score itself.
    """
    seg = y
    if len(y) > int(2.5e-3 * fs):
        c0 = np.abs(_corr(y, cfg, fs, cfg.chip_s))
        i0 = max(0, int(np.argmax(c0)) - int(1.25e-3 * fs))
        seg = y[i0:i0 + int(2.5e-3 * fs)]
    scored = []
    for T in np.arange(cfg.rate_search_lo, cfg.rate_search_hi,
                        cfg.rate_search_step) * cfg.chip_s:
        c = np.abs(_corr(seg, cfg, fs, T))
        if len(c) < 10:
            continue
        scored.append((float(T),
                       _comb_energy(c, cfg.pn_len * T * fs, cfg.n_symbols)))
    scored.sort(key=lambda r: -r[1])
    picked = []
    for T, _ in scored:
        if all(abs(T / t - 1.0) > 0.01 for t, _ in picked):
            picked.append((T, 0.0))
        if len(picked) >= cfg.n_rate_candidates:
            break
    return [t for t, _ in picked]


def _comb_teeth(c: np.ndarray, fs: float, cfg: UplinkConfig, chip_s: float):
    """Comb-extract despread symbols from a correlation surface.

    The symbol comb is anchored at the global correlation max (parabolic
    refined) and spaced pn_len*chip_s; free-running peak picking is avoided -
    on the real channel its positions jitter by us against chip-level
    correlation ripples. Returns (times_s, values, quality).
    """
    mag = np.abs(c)
    if len(c) < cfg.n_symbols + 2:
        return np.array([]), np.array([]), 0.0
    p0 = int(np.argmax(mag))
    if 0 < p0 < len(c) - 1:
        a, b, d = mag[p0 - 1], mag[p0], mag[p0 + 1]
        den = a - 2 * b + d
        if den != 0:
            off = 0.5 * (a - d) / den
            if abs(off) <= 1:
                p0 = p0 + off
    step = cfg.pn_len * chip_s * fs
    n_lo = int(np.ceil(-p0 / step)) - 2
    n_hi = int(np.floor((len(c) - 1 - cfg.window_completeness * step
                         - p0) / step)) + 2
    n = np.arange(n_lo, n_hi + 1)
    pos_f = p0 + n * step
    # keep only teeth whose 32-chip correlation window is >=90% inside the
    # surface: edge-truncated windows give junk values; and drop (never
    # clip) out-of-range teeth - clipped duplicates corrupt frame edges
    ok = (pos_f >= 0) & (pos_f <= len(c) - 1
                          - cfg.window_completeness * step)
    pos = np.round(pos_f[ok]).astype(int)
    cand = pos[:, None] + np.array([-1, 0, 1])[None, :]
    cand = np.clip(cand, 0, len(c) - 1)
    pick = cand[np.arange(len(pos)), np.argmax(np.abs(c[cand]), axis=1)]
    sigma = _robust_sigma(mag)
    quality = (float(mag.max()) - float(np.median(mag))) / (sigma + 1e-12)
    return pick / fs, c[pick], float(quality)


def symbol_peaks(y: np.ndarray, fs: float, cfg: UplinkConfig,
                 chip_s: float | None = None):
    """Correlate then comb-extract symbols: (times_s, values, quality)."""
    chip_s = chip_s or cfg.chip_s
    return _comb_teeth(_corr(y, cfg, fs, chip_s), fs, cfg, chip_s)


def energy_bursts(cmag: np.ndarray, fs: float, cfg: UplinkConfig,
                  chip_s: float, k: float | None = None,
                  min_len_mult: float | None = None):
    """Signal time-spans from symbol-windowed energy of |corr|.

    Detection is decoupled from comb alignment: smoothing |corr| over one
    symbol lights up whole bursts even when the global comb has walked off
    under clock drift. Dual-mode adaptive threshold, never fixed.
    """
    win = max(1, int(round(cfg.pn_len * chip_s * fs)))
    # max-pool (not mean): an m-sequence's near-zero off-peak correlation
    # makes the mean-smoothed energy beat between the boxcar length and the
    # fractional-sample symbol period, fragmenting continuous signals
    e = maximum_filter1d(cmag, size=win, mode="nearest")
    if len(e) == 0:
        return []
    p50, p99 = float(np.percentile(e, 50)), float(np.percentile(e, 99))
    if p50 > cfg.cont_thr_frac * p99:  # continuous mode
        thr = cfg.cont_thr_frac * p99
    else:
        # burst mode: MAD rule plus a relative floor - when the capture is
        # mostly deep silence the MAD collapses toward 0 and residual
        # speckle energy bridges into one giant span
        k = cfg.burst_k_mad if k is None else k
        thr = max(float(np.median(e)) + k * _robust_sigma(e),
                  cfg.burst_floor_frac * p99)
    hot = e > thr
    spans, start = [], None
    gap = 0
    for i, v in enumerate(hot):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > cfg.burst_gap_symbols * win:
                spans.append((start, i - gap))
                start, gap = None, 0
    if start is not None:
        spans.append((start, len(hot) - 1))
    min_len_mult = (cfg.burst_min_symbols if min_len_mult is None
                    else min_len_mult)
    return [(a / fs, b / fs) for a, b in spans
            if (b - a) >= min_len_mult * cfg.pn_len * chip_s * fs]


def _sync_phase(vals: np.ndarray, cfg: UplinkConfig):
    """Best (start_offset, polarity) for the preamble inside one burst."""
    n = len(vals)
    exp = cfg.expected_preamble_signs()
    best, best_key = -np.inf, (0, 1)
    for pol in (1, -1):
        for s in range(0, max(1, n - cfg.n_symbols + 2)):
            n_frames = (n - s) // cfg.n_symbols
            if n_frames < 1:
                continue
            score = 0.0
            for f in range(n_frames):
                base = s + f * cfg.n_symbols
                score += float(np.dot(pol * exp, vals[base:base + len(exp)]))
            if score > best:
                best, best_key = score, (s, pol)
    return best_key


def _comb_energy(cmag: np.ndarray, step_f: float, n_min: int) -> float:
    """Bidirectional comb energy (capped at 8 back / 32 forward teeth).

    Teeth are sampled at FLOAT comb positions (rounded), matching
    _comb_teeth. A short forward-only comb cannot discriminate a small
    chip-period error (peak width ~1 chip); scoring both directions from
    the anchor doubles the effective walk for short captures.
    """
    if step_f < 1:
        return 0.0
    p0 = int(np.argmax(cmag))
    n_back = min(8, int(p0 // step_f))
    n_fwd = min(32, int((len(cmag) - 1 - p0) // step_f))
    if n_back + n_fwd < 6:
        return 0.0  # too few teeth in either direction to judge a period
    n = np.arange(-n_back, n_fwd + 1)
    pos = np.round(p0 + n * step_f).astype(int)
    pos = pos[(pos >= 0) & (pos < len(cmag))]
    return float(cmag[pos].sum()) if len(pos) else 0.0


def _refine_comb(cv: np.ndarray, pick: np.ndarray, fs: float,
                 cfg: UplinkConfig | None = None):
    """Sub-sample tooth positions (parabolic) + least-squares comb fit.

    Returns (anchor_sample, step_samples) refined, or None if the fit is
    degenerate. Sample-grid comb scoring has a one-sample plateau; the LSQ
    fit over sub-sample tooth positions resolves T far below one sample.
    """
    mag = np.abs(cv)
    sub = []
    cut = (cfg.tooth_strong_frac if cfg else 0.25)
    strong = (mag[pick] > cut
              * float(np.max(mag[pick]))) if len(pick) else None
    for p, ok in zip(pick, strong):
        p = int(p)
        if not ok or p <= 0 or p >= len(mag) - 1:
            continue
        a, b, c = mag[p - 1], mag[p], mag[p + 1]
        den = a - 2 * b + c
        d = 0.5 * (a - c) / den if den != 0 else 0.0
        if abs(d) <= 1.5:  # real teeth sit on interference ripple: wider
            sub.append(p + d)
    if len(sub) < 4:
        return None
    t = np.array(sub)
    n = np.round((t - t[0]) / np.median(np.diff(t) if len(t) > 1 else 1))
    n = np.maximum.accumulate(n)  # enforce monotone tooth indices
    if len(np.unique(n)) < 4:
        return None
    step, anchor = np.polyfit(n, t, 1)
    if not 0.5 * np.median(np.diff(t)) < step < 2 * np.median(np.diff(t)):
        return None
    return float(anchor), float(step)


def decode_span(y: np.ndarray, fs: float, cfg: UplinkConfig,
                t_lo: float, t_hi: float, chip0: float):
    """Decode one burst span: fine chip-period re-estimate around chip0,
    then anchor-refined comb extraction, sync and frame decode."""
    sym = cfg.pn_len * chip0
    m = cfg.seg_margin_symbols
    i0 = max(0, int((t_lo - m * sym) * fs))
    i1 = min(len(y), int((t_hi + m * sym) * fs) + 1)
    seg = y[i0:i1]
    if len(seg) < int(cfg.n_symbols * sym * fs):
        return [], chip0
    # fine per-burst chip sweep (coarse) then sub-sample LSQ comb refinement
    best_chip, best_score = chip0, -1.0
    lo, hi, n = 1 - cfg.span_sweep_frac, 1 + cfg.span_sweep_frac, \
        cfg.span_sweep_points
    for T in chip0 * np.linspace(lo, hi, n):
        cm = np.abs(_corr(seg, cfg, fs, T))
        if len(cm) < cfg.n_symbols + 2:
            continue
        sc = _comb_energy(cm, cfg.pn_len * T * fs, cfg.n_symbols)
        if sc > best_score:
            best_chip, best_score = float(T), sc
    cv = _corr(seg, cfg, fs, best_chip)
    # sub-sample refinement: parabolic tooth positions -> LSQ comb fit
    mag0 = np.abs(cv)
    if len(mag0) > 8:
        p0 = int(np.argmax(mag0))
        step0 = cfg.pn_len * best_chip * fs
        n_rng = np.arange(-min(8, int(p0 // step0)),
                          min(32, int((len(mag0) - 1 - p0) // step0)) + 1)
        pick0 = np.round(p0 + n_rng * step0).astype(int)
        pick0 = pick0[(pick0 >= 0) & (pick0 < len(mag0))]
        fit = _refine_comb(cv, pick0, fs, cfg)
        if fit is not None:
            anchor, step_fit = fit
            chip_fit = step_fit / (cfg.pn_len * fs)
            if (1 - cfg.lsq_accept_frac) < chip_fit / chip0 \
                    < (1 + cfg.lsq_accept_frac):
                best_chip = float(chip_fit)
                cv = _corr(seg, cfg, fs, best_chip)
    times, vals, _ = _comb_teeth(cv, fs, cfg, best_chip)
    times = times + i0 / fs
    if len(vals) < cfg.n_symbols:
        return [], best_chip
    # trim pre-burst noise teeth (leading contiguity by magnitude) and any
    # teeth beyond the energy span; weak-but-real edge teeth inside the span
    # must survive (they carry data bits)
    mag = np.abs(vals)
    hot = mag > cfg.tooth_strong_frac * float(np.percentile(mag, 95))
    if np.any(hot):
        first = int(np.argmax(hot))
        times, vals, mag = times[first:], vals[first:], mag[first:]
    keep_t = times <= t_hi + cfg.tail_margin_symbols * sym
    times, vals = times[keep_t], vals[keep_t]
    if len(vals) < cfg.n_symbols:
        return [], best_chip
    s, pol = _sync_phase(vals, cfg)
    ref = np.median(np.abs(vals)) or 1.0
    exp = cfg.expected_preamble_signs()
    frames = []
    for f in range((len(vals) - s) // cfg.n_symbols):
        base = s + f * cfg.n_symbols
        zs = pol * vals[base:base + cfg.n_symbols]
        data = tuple(1 if v > 0 else 0 for v in zs[len(exp):])
        val = 0
        for bit in data:
            val = (val << 1) | bit
        frames.append(FrameResult(
            idx=0,
            t_start=float(times[base]),
            pream_ok=bool(np.all(np.sign(zs[:len(exp)]) == np.sign(exp))),
            data_bits=data,
            data_hex=f"0x{val:02X}",
            mean_conf=float(np.mean(np.abs(zs[len(exp):])) / ref),
        ))
    return frames, (s, pol), best_chip


# ---------------------------------------------------------------- top level

def _decode_at(y2: np.ndarray, fs2: float, cfg: UplinkConfig,
               chip_est: float) -> UplinkResult:
    """One full decode attempt at a given chip period."""
    res = UplinkResult(symbol_s_est=chip_est * cfg.pn_len)
    if abs(chip_est / cfg.chip_s - 1.0) > 0.01:
        res.warnings.append(
            f"measured chip period {chip_est*1e6:.4f} us deviates "
            f"{(chip_est/cfg.chip_s-1)*100:+.2f}% from nominal "
            f"{cfg.chip_s*1e6:.4f} us - decoding at measured rate")

    cv = _corr(y2, cfg, fs2, chip_est)
    cmag = np.abs(cv)
    times, vals, quality = _comb_teeth(cv, fs2, cfg, chip_est)
    res.quality = quality
    if len(times) < cfg.n_symbols:
        res.warnings.append(
            f"no uplink signal found ({len(times)} comb symbols, "
            f"quality {quality:.1f})")
        return res

    # burst time-spans from correlation energy (decoupled from comb walk)
    spans = energy_bursts(cmag, fs2, cfg, chip_est)
    res.n_bursts = len(spans)
    if len(times):
        sel = np.zeros(len(times), dtype=bool)
        for lo, hi in spans:
            sel |= (times >= lo - 1.5 * cfg.symbol_s) & \
                   (times <= hi + 1.5 * cfg.symbol_s)
        res.z = vals[sel]
        res.z_times = times[sel]
        res.t0 = float(times[sel][0]) if np.any(sel) else float(times[0])
    if not spans:
        res.warnings.append("correlation found but no burst span")
        return res

    n_bad = 0
    chip_meas = []
    for bi, (t_lo, t_hi) in enumerate(spans):
        out = decode_span(y2, fs2, cfg, t_lo, t_hi, chip_est)
        frames, extra = out[0], out[1]
        chip_b = out[-1]
        chip_meas.append(chip_b)
        for fr in frames:
            fr.idx = len(res.frames)
            fr.burst = bi
            n_bad += 0 if fr.pream_ok else 1
            res.frames.append(fr)
        if isinstance(extra, tuple):
            res.phase, res.polarity = extra
    if chip_meas:
        res.symbol_s_est = float(np.median(chip_meas)) * cfg.pn_len
    if not res.frames:
        res.warnings.append("bursts detected but no complete frame inside")
    else:
        ok = sum(1 for f in res.frames if f.pream_ok)
        if ok < cfg.pream_pass_gate * len(res.frames):
            # noise that slipped the chip-wander gate decodes to random bits
            # with a broken preamble pattern; never present it as traffic
            res.warnings.append(
                f"preamble passed on only {ok}/{len(res.frames)} frames - "
                "treating capture as garbage")
            res.frames = []
        elif ok < cfg.pream_warn_gate * len(res.frames):
            res.warnings.append(
                f"preamble check failed on {len(res.frames)-ok}/"
                f"{len(res.frames)} frames")
    return res


def decode_uplink(y: np.ndarray, fs: float,
                  cfg: UplinkConfig | None = None) -> UplinkResult:
    """Decode an uplink capture: candidate chip periods from the comb sweep
    are each attempted; the preamble check arbitrates which one is real
    (on the live channel interference can outscore the true period)."""
    cfg = cfg or UplinkConfig()
    y = np.asarray(y, dtype=np.float64)
    y2, fs2 = precondition(y, fs, cfg)

    cands = [t for t in chip_candidates(y2, fs2, cfg)
             if abs(t / cfg.chip_s - 1.0) <= cfg.rate_gate]
    if not any(abs(t / cfg.chip_s - 1.0) < 1e-9 for t in cands):
        cands.append(cfg.chip_s)
    best = None
    best_ok = -1
    for t in cands:
        res = _decode_at(y2, fs2, cfg, t)
        ok = sum(1 for f in res.frames if f.pream_ok)
        if res.frames and ok >= cfg.pream_pass_gate * len(res.frames):
            return res
        if ok > best_ok:
            best, best_ok = res, ok
    if best is None:
        best = _decode_at(y2, fs2, cfg, cfg.chip_s)
    if not best.frames:
        best.warnings.append(
            "no candidate chip period "
            f"({', '.join(f'{t*1e6:.3f}' for t in cands)} us) "
            "yielded a valid preamble")
    return best

