"""下行 DBPSK 接收机（vendored，算法零改动）。

出处: 原vendored工程/dpsk_rx.py（2026-08 实机调参版）。
协议: 263kHz 方波载波 DBPSK、10 载波周期/bit、每包 1 起始位（相位参考）+ 16 数据位；
每上行帧周期 6 个包，槽位挂在上行 60Hz 网格上（delta 自校准——固件偏移参考点与
线上实测差 ~1.8ms，绝不假设）。解调 = 延迟线鉴相（z_m = ∫y(t)·y(t−Tb)，无需载波相位），
逐包 fc 估计 + 双簇共识仲裁 + 槽位角色轮转（恒载波包定位对端周期末位）。

按 ADR-011 搬运：参数全量可配（DownlinkConfig/DOWNLINK_PROFILES），DSP 零改动。
"""

from __future__ import annotations

"""Stylus downlink receiver: 263 kHz nominal square-carrier DBPSK, 10 cycles/bit,
16 bits/packet (640 us on air), 6 packets per uplink frame period.

Timing model: downlink slots sit at (uplink frame grid + delta) + k*slot_s.
The nominal offsets (1970/4748/... us) are firmware numbers referenced to
the TP's own cycle start; on the wire the first packet lands ~1.8 ms later
than "DSSS frame start + 1970 us", so delta is self-calibrated per capture
by energy scan - never assumed.

Demodulation: classic delay-line discriminator z_m = integral of
y(t)*y(t - Tb) over bit m. It needs no carrier phase and no absolute
phase reference, exactly matching DBPSK on a square carrier: same polarity
as the previous bit -> positive product, flipped -> negative. The carrier
frequency (and thus Tb = 9/fc) is estimated per packet from the bandpassed
segment; the bit grid phase is found by maximizing the summed |z| over a
sub-bit search.
"""

from dataclasses import dataclass, field, replace
from collections import Counter

import numpy as np
from scipy.signal import firwin

from ..uplink.dsss import UplinkConfig


@dataclass(frozen=True)
class DownlinkConfig:
    # ---- protocol shape (per profile) ----
    name: str = "default"      # transmitter/profile identity
    fc_nominal: float = 263e3
    cycles_per_bit: int = 10   # 1 bit = 10 carrier cycles (user-corrected)
    n_bits: int = 17           # 1 start bit (phase reference) + 16 data
    slot_offsets_s: tuple = (1970e-6, 4748e-6, 7525e-6,
                             10303e-6, 13081e-6, 15858e-6)
    frame_period_s: float = 1.0 / 60.0  # uplink burst grid period
    band_hz: tuple = (150e3, 420e3)
    invert_polarity: bool = False  # differential sense: 1 = phase flip

    # ---- strategy hyperparameters (defaults tuned on the live channel) ----
    fc_search_lo: float = 0.88      # per-packet fc sweep, x nominal
    fc_search_hi: float = 1.12
    fc_search_points: int = 49
    fc_polish_frac: float = 0.005   # winner polish range
    fc_polish_points: int = 11
    offset_search_frac: float = 0.4   # bit-grid offset search, x Tb
    offset_step_frac: float = 0.05
    weak_tooth_pct: float = 20        # demod score percentile of |z|
    edge_level: float = 0.5           # envelope edge level, x p90 plateau
    edge_smooth_s: float = 5e-6
    bp_taps: int = 301            # bandpass length; raise for close carriers
    fc_cluster_hz: float = 1e3        # consensus clustering resolution
    cluster2_ratio: float = 0.3       # 2nd cluster must reach this of top
    consensus_sample: int = 24        # packets sampled for cluster arbiter
    const_max_flips: int = 1          # <= this many flips = constant packet

    @property
    def n_slots(self) -> int:
        return len(self.slot_offsets_s)

    @property
    def slot_period_s(self) -> float:
        return self.frame_period_s / self.n_slots


# named protocol profiles: extend here as new downlink shapes appear,
# e.g. "dl_b": dict(fc_nominal=300e3, cycles_per_bit=8, n_bits=13,
#                   slot_offsets_s=(...), frame_period_s=1/120.)
DOWNLINK_PROFILES = {
    "default": {},
}


def downlink_profile(profile: str = "default", **overrides) -> DownlinkConfig:
    if profile not in DOWNLINK_PROFILES:
        raise KeyError(f"unknown downlink profile {profile!r}; "
                       f"have {sorted(DOWNLINK_PROFILES)}")
    cfg = DownlinkConfig(**DOWNLINK_PROFILES[profile])
    overrides.setdefault("name", profile)
    return replace(cfg, **overrides)


@dataclass
class PacketResult:
    frame: int          # index of the uplink frame this slot belongs to
    slot: int           # 0..5
    t_start: float      # seconds, capture timeline
    fc_est: float
    bits: tuple         # 17 symbol signs as 0/1 (bit0 = start reference)
    diff_bits: tuple    # 16 data bits (1 = phase flip vs previous symbol)
    data_hex: str       # hex of diff_bits (phase-flip = 1)
    data_hex_inv: str   # hex with inverted differential sense
    mean_conf: float
    score: float = 0.0


@dataclass
class DownlinkResult:
    packets: list = field(default_factory=list)
    delta_s: float = 0.0       # measured anchor offset from uplink frame grid
    fc_est: float = 0.0
    zero_slot: int = -1         # raw slot index carrying the constant packet
    warnings: list = field(default_factory=list)


def _bandpass(y, fs, cfg: DownlinkConfig):
    """Zero-phase FIR bandpass. A narrow IIR here rings for ~100 us around
    each packet edge (filtfilt doubles the order at 10 MSa/s) and smears
    edge-based timing. With several transmitters the carriers can sit close:
    raise bp_taps for a sharper transition (3.3*fs/taps)."""
    lo, hi = cfg.band_hz
    hi = min(hi, 0.45 * fs)
    if lo >= hi:
        return y
    taps = min(cfg.bp_taps, 2 * (len(y) // 2) + 1)
    if taps < 33:
        return y
    h = firwin(taps, [lo, hi], pass_zero=False, fs=fs)
    if len(y) > 50_000:  # FFT convolution for capture-length inputs
        from scipy.signal import fftconvolve
        return fftconvolve(y, h, mode="same")
    return np.convolve(y, h, mode="same")


def _estimate_fc(y, fs, cfg: DownlinkConfig) -> float:
    """Carrier frequency from a heavily zero-padded FFT peak.

    On real packets the crossing-rate estimator biases 2-5% low (edges,
    noise, quantisation) and the per-packet refinement cannot recover,
    which scrambles the 16-bit grid. Padding to 2^17 gives ~76 Hz
    resolution on a 16-bit packet.
    """
    n = 1 << 17
    seg = y[int(0.05 * len(y)):int(0.95 * len(y))]
    if len(seg) < 64:
        return 0.0
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    m = (freqs > cfg.band_hz[0]) & (freqs < cfg.band_hz[1])
    if not np.any(m):
        return 0.0
    idx = np.where(m)[0]
    k = idx[np.argmax(spec[idx])]
    if 0 < k < len(spec) - 1:
        a, b, c = (np.log(max(v, 1e-12)) for v in (spec[k - 1], spec[k], spec[k + 1]))
        den = a - 2 * b + c
        d = 0.5 * (a - c) / den if den else 0.0
        d = max(-1.0, min(1.0, d))
        k = k + d
    return float(k / n * fs)


def estimate_anchor_delta(y2, fs, frame_starts, cfg: DownlinkConfig,
                          up_cfg: UplinkConfig) -> float:
    """Per-profile anchor: scan delta over one slot period and score how
    well ALL of this profile's slot offsets light up across frames.

    With multiple transmitters the first packet edge after the frame can
    belong to another profile, so edge order is meaningless; the slot SET
    (this profile's own offsets, repeated every frame) is the signature.
    """
    slot_period = cfg.slot_period_s
    yb = np.abs(_bandpass(y2, fs, cfg))
    smooth = max(3, int(60e-6 * fs))
    e = np.convolve(yb, np.ones(smooth) / smooth, mode="same")
    win = max(3, int(0.3 * slot_period * fs))  # packet-sized energy core
    offs_rel = [o - cfg.slot_offsets_s[0] for o in cfg.slot_offsets_s]
    starts = frame_starts[:12] if len(frame_starts) >= 12 else frame_starts
    steps = 120
    best_d, best_e = 0.0, -1.0
    for i in range(steps):
        d = (i / steps) * slot_period
        s = 0.0
        for t0 in starts:
            for r in offs_rel:
                i0 = int(round((t0 + d + r) * fs))
                i1 = i0 + win
                if 0 <= i0 and i1 < len(e):
                    s += float(np.mean(e[i0:i1] ** 2))
        if s > best_e:
            best_e, best_d = s, d
    # refine on the sharp packet-start edge inside the coarse window (the
    # energy plateau is packet-width wide; the rising edge is not)
    med = float(np.median(e))
    mad = 1.4826 * float(np.median(np.abs(e - med))) + 1e-12
    thr = med + 8 * mad
    half = win  # plateau can span a full window before the true edge
    edges = []
    for t0 in starts[:6]:
        lo = int((t0 + best_d) * fs) - half
        hi = int((t0 + best_d) * fs) + half
        lo = max(0, lo)
        if hi - lo < 10 or hi > len(e):
            continue
        seg = e[lo:hi] > thr
        idx = np.argmax(seg)
        if seg[idx]:
            edges.append((lo + idx) / fs - t0)
    if edges:
        return float(np.median(edges)) - 0.5 * smooth / fs
    return float(best_d)


def up_cfg_period(up_cfg: UplinkConfig) -> float:
    """Frame grid period (downlink config drives it via frame_period_s)."""
    return getattr(up_cfg, "frame_period_s", 1.0 / 60.0)


def decode_packet(y2, fs, t_start, cfg: DownlinkConfig,
                  fc_hint: float | None = None) -> PacketResult | None:
    """DBPSK-demodulate one packet starting near t_start.

    fc_hint: capture-consensus carrier to lock to (refined +-0.5%);
    without it a full-range fc search runs (per-packet sideband
    ambiguity - see decode_downlink)."""
    tb = cfg.cycles_per_bit / cfg.fc_nominal
    i0 = max(0, int((t_start - 2 * tb) * fs))
    i1 = min(len(y2), int((t_start + (cfg.n_bits + 3) * tb) * fs))
    seg = y2[i0:i1]
    if len(seg) < int((cfg.n_bits + 1) * tb * fs):
        return None
    yb = _bandpass(seg, fs, cfg)
    fc0 = _estimate_fc(yb, fs, cfg)
    if not cfg.band_hz[0] * 0.8 < fc0 < cfg.band_hz[1] * 1.2:
        return None  # silent/misplaced window: no carrier to demod

    # anchor the bit grid at the packet's envelope rising edge (50% point):
    # a free offset search has whole/half-bit ambiguities where a misaligned
    # grid still produces uniform-magnitude delay products
    k = max(3, int(cfg.edge_smooth_s * fs))
    env = np.convolve(np.abs(yb), np.ones(k) / k, mode="same")
    plateau = float(np.percentile(env, 90))
    # (edge level fraction applied below)
    if plateau <= 0:
        return None
    above = env > cfg.edge_level * plateau
    i_edge = int(np.argmax(above))  # first sample above half plateau
    if not above[i_edge]:
        return None
    edge_off = i_edge  # absolute index in the segment
    # packet end: constant-carrier packets carry almost no grid information
    # (every delay product is coherent), so their fc estimate drifts worst
    # and the LAST symbol's window can overrun into post-packet silence
    on = np.where(above)[0]
    i_end = int(on[-1]) if len(on) else len(yb) - 1

    def demod(tb, offset):
        """z_m for each bit given bit period and sub-sample start offset."""
        base = edge_off + offset
        step = int(tb * fs)
        d = step
        z = np.zeros(cfg.n_bits)
        for m in range(cfg.n_bits):
            a0 = base + m * step
            a1 = min(a0 + step, i_end + 1)  # stay inside the packet
            if a1 - a0 < step // 2 or a1 >= len(yb) or a0 - d < 0:
                return None
            z[m] = np.dot(yb[a0:a1], yb[a0 - d:a1 - d])
        return z

    def best_for(fc):
        tb = cfg.cycles_per_bit / fc
        z, score = None, -1.0
        rng_off = range(-int(cfg.offset_search_frac * tb * fs),
                        int(cfg.offset_search_frac * tb * fs),
                        max(1, int(cfg.offset_step_frac * tb * fs)))
        for off in rng_off:
            zz = demod(tb, off)
            if zz is None:
                continue
            sc = float(np.percentile(np.abs(zz[1:]),
                                    cfg.weak_tooth_pct))
            if sc > score:
                score, z = sc, zz
        return score, z

    # two-stage fc search: a DBPSK packet has strong spectral sidebands at
    # fc +- 1/(2Tb) (~12.5 kHz) that can lure an FFT or crossing estimator
    # 5% off, beyond any local refinement; the demod metric itself (uniform
    # strong products across all 16 bits) is the only reliable arbitrator.
    # With fc_hint (capture consensus) only a tight polish is done.
    fc_best, z_best, score_best = fc_hint or fc0, None, -1.0
    if fc_hint:
        for fc in fc_hint * np.linspace(1 - cfg.fc_polish_frac,
                                        1 + cfg.fc_polish_frac,
                                        cfg.fc_polish_points):
            sc, zz = best_for(fc)
            if zz is not None and sc > score_best:
                score_best, fc_best, z_best = sc, float(fc), zz
    else:
        for fc in cfg.fc_nominal * np.linspace(
                cfg.fc_search_lo, cfg.fc_search_hi, cfg.fc_search_points):
            sc, zz = best_for(fc)
            if zz is not None and sc > score_best:
                score_best, fc_best, z_best = sc, float(fc), zz
        for fc in fc_best * np.linspace(1 - cfg.fc_polish_frac,
                                        1 + cfg.fc_polish_frac,
                                        cfg.fc_polish_points):
            sc, zz = best_for(fc)
            if zz is not None and sc > score_best:
                score_best, fc_best, z_best = sc, float(fc), zz
    if z_best is None:
        return None
    fc, z, score = fc_best, z_best, score_best
    # z_m = A_m * A_{m-1} * |z|: its sign IS the flip indicator between
    # adjacent symbols (z_0 compares against pre-packet silence -> junk).
    # XOR-ing consecutive z-signs would compare bits TWO apart.
    signs = tuple(1 if v > 0 else 0 for v in z)
    diffs = tuple(1 if v < 0 else 0 for v in z[1:])
    if cfg.invert_polarity:
        diffs = tuple(1 - b for b in diffs)
    val = 0
    for b in diffs:
        val = (val << 1) | b
    vali = 0
    for b in diffs:
        vali = (vali << 1) | (1 - b)
    med = np.median(np.abs(z)) or 1.0
    return PacketResult(
        frame=0, slot=0,
        t_start=t_start, fc_est=fc, bits=signs, diff_bits=diffs,
        data_hex=f"0x{val:04X}", data_hex_inv=f"0x{vali:04X}",
        mean_conf=float(np.mean(np.abs(z[1:])) / med),
        score=float(score),
    )


def decode_downlink(y2, fs, frame_starts, cfg: DownlinkConfig | None = None,
                    up_cfg: UplinkConfig | None = None) -> DownlinkResult:
    """Decode all downlink packets referenced to the uplink frame grid.

    `frame_starts`: uplink frame start times on the SAME capture timeline
    and sample grid as y2 (use sample-index alignment across channels).
    """
    cfg = cfg or DownlinkConfig()
    up_cfg = up_cfg or UplinkConfig()
    res = DownlinkResult()
    if len(frame_starts) < 1:
        res.warnings.append("no uplink frame starts for downlink anchoring")
        return res
    period = cfg.frame_period_s
    grid = _fit_grid(frame_starts, period,
                     t_end=min(len(y2) / fs, max(frame_starts) + 1.2 * period))
    res.delta_s = estimate_anchor_delta(y2, fs, grid, cfg, up_cfg)
    res.fc_est = cfg.fc_nominal

    slots = []
    for fi, t_frame in enumerate(grid):
        for k, off in enumerate(cfg.slot_offsets_s):
            # user offsets are firmware numbers; the measured delta already
            # carries the reference discrepancy, so slots are placed on the
            # measured anchor grid with the nominal spacing structure
            t_slot = t_frame + res.delta_s + (off - cfg.slot_offsets_s[0])
            if t_slot < 0 or t_slot + 0.001 > len(y2) / fs:
                continue
            slots.append((fi, k, t_slot))

    # pass 1: per-packet fc search; the DBPSK sideband ambiguity splits the
    # estimates into disjoint clusters decoding to disjoint value sets
    first = []
    for fi, k, t_slot in slots:
        pk = decode_packet(y2, fs, t_slot, cfg)
        if pk is not None:
            pk.frame, pk.slot = fi, k
            first.append(pk)
    fc_hint = None
    if len(first) >= cfg.n_slots:
        top2 = Counter(round(p.fc_est / cfg.fc_cluster_hz)
                       for p in first).most_common(2)
        if len(top2) == 2 and \
                top2[1][1] >= cfg.cluster2_ratio * top2[0][1]:
            centers = [c * 1e3 for c, _ in top2]
            totals = []
            for fc_c in centers:
                ssum, n = 0.0, 0
                for pk in first[:: max(1, len(first)
                                       // cfg.consensus_sample)]:
                    q = decode_packet(y2, fs, pk.t_start, cfg, fc_hint=fc_c)
                    if q is not None:
                        ssum += q.score
                        n += 1
                totals.append(ssum / max(1, n))
            fc_hint = centers[int(np.argmax(totals))]

    # pass 2 (or only pass): decode every slot at the consensus carrier
    for fi, k, t_slot in slots:
        pk = decode_packet(y2, fs, t_slot, cfg, fc_hint=fc_hint)
        if pk is None:
            continue
        pk.frame, pk.slot = fi, k
        res.packets.append(pk)
    if not res.packets:
        res.packets = first  # fall back to per-packet estimates
    if not res.packets:
        res.warnings.append("no downlink packets decoded")
        return res
    res.fc_est = float(np.median([p.fc_est for p in res.packets]))

    # slot-role calibration: the constant-carrier packet (<=1 phase flip)
    # belongs LAST in the user's cycle. The firmware slot offsets are
    # referenced to a point ~12.9 ms before the DSSS burst, so temporal
    # order from the first packet after the burst = user slots 4,5,0,..,3;
    # detect the constant slot and rotate labels + regroup cycles.
    def _near_const(p):
        b = p.diff_bits
        return sum(1 for a, c in zip(b, b[1:]) if a != c) \
            <= cfg.const_max_flips

    n_slots = cfg.n_slots
    frac = {}
    for s in range(n_slots):
        grp = [p for p in res.packets if p.slot == s]
        if len(grp) >= 3:
            frac[s] = float(np.mean([_near_const(p) for p in grp]))
    zero_slot = max(frac, key=frac.get) if frac else n_slots - 1
    res.zero_slot = zero_slot
    if zero_slot != n_slots - 1:
        by_frame = {}
        for p in res.packets:
            by_frame.setdefault(p.frame, {})[p.slot] = p
        fids = sorted(by_frame)
        rotated = []
        for n in fids:
            for k in range(n_slots):
                js = (zero_slot + 1 + k) % n_slots
                src = n if js > zero_slot else n + 1
                p = by_frame.get(src, {}).get(js)
                if p is not None:
                    rotated.append(PacketResult(
                        frame=n, slot=k, t_start=p.t_start, fc_est=p.fc_est,
                        bits=p.bits, diff_bits=p.diff_bits,
                        data_hex=p.data_hex, data_hex_inv=p.data_hex_inv,
                        mean_conf=p.mean_conf, score=p.score))
        if rotated:
            ids = {}
            for q in rotated:
                ids.setdefault(q.frame, len(ids))
                q.frame = ids[q.frame]
            res.packets = rotated
    return res


def decode_downlink_multi(y2, fs, frame_starts,
                          configs: list) -> dict:
    """Decode several concurrent transmitters from one capture.

    Each config is fully independent: its own band, anchor (slot-set
    scan), fc consensus and slot-role rotation. Transmitters must be
    separable by frequency (distinct fc + non-overlapping band_hz) and/or
    by time (distinct slot_offsets_s); same-band same-slots pairs are
    physically colliding and cannot be resolved by any receiver.
    """
    out = {}
    for cfg in configs:
        res = decode_downlink(y2, fs, frame_starts, cfg)
        out[cfg.name] = res
    return out


def _fit_grid(frame_starts, period: float, t_end: float) -> np.ndarray:
    """Reconstruct the uniform frame grid from decoded (gappy) starts."""
    t = np.sort(np.asarray(frame_starts, dtype=np.float64))
    n = np.round((t - t[0]) / period).astype(np.int64)
    if len(np.unique(n)) >= 2:
        slope, intercept = np.polyfit(n, t, 1)
    else:
        slope, intercept = period, t[0]
    n_lo = int(np.floor(-intercept / slope + 1e-6))
    n_hi = int(np.ceil((t_end - intercept) / slope - 1e-6))
    return intercept + np.arange(n_lo, n_hi + 1) * slope
