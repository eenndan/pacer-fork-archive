"""Corner model: curvature-based corner detection + per-corner lap analysis (F-corner).

PACER-FREE BY CONTRACT (numpy only). Fed by Session's per-lap arrays; Session owns the
caching/invalidations and the views consume the results through it, so neither this module
nor the views ever import the compiled `pacer` bindings (the studio architecture rule).

NOT MAP-MATCHING. Everything here runs on OUR OWN measured (cleaned + smoothed) GPS trace:
curvature is differentiated from the trace's own heading, the threshold is derived from the
trace's own curvature distribution, and corner windows live in the session's own best-lap
normalized-distance space. The rejected 2026-06 experiment was fitting our trace to an
EXTERNAL reference centerline — no external geometry is involved anywhere in this model.

The model, in order:
  1. Per lap: heading = unwrap(atan2(dy, dx)) on the local-frame trace, differentiated vs
     arc length -> curvature kappa(s) (1/m, signed: + = left), boxcar-smoothed over
     KAPPA_SMOOTH_M of arc (reusing _signal._smooth).
  2. The track profile = the MEDIAN kappa over the session's clean laps, sampled on the
     best lap's normalized-distance grid. The median averages out line choice + GPS noise:
     measured on the two D24 validation recordings, best-lap-only detection put the same
     corner apexes up to 29.5 m apart across recordings (the driver drives a different line
     through flat-radius arcs each lap); the median profile brings that to <= 3.5 m.
  3. Threshold from the track's OWN |kappa| distribution (no magic constant): Otsu's
     between-class-variance split in LOG10 space — see derive_threshold for the measured
     distributions that motivate the log domain.
  4. Corner spans = contiguous |kappa| >= threshold with hysteresis, split at sign changes
     (S-complexes are separate corners with opposite directions), merged across sub-gap
     jitter, and filtered by minimum arc length and minimum integrated turn angle.
  5. Each corner: enter/exit/apex odometer distances on the best lap + direction
     (sign of kappa). Apex = the |kappa|-weighted centroid of the span (== the |kappa| max
     for a peaked corner, but stable on flat-topped sweepers where the max position is
     line-noise: measured cross-recording apex agreement improved from 13.3 m worst-case
     with the raw argmax to 3.5 m with the centroid).

Projection (lap_corner_stats / segment_times): corner windows are FRACTIONS of the
reference (best) lap's odometer and are projected onto every lap by normalized distance —
exactly how Session.lap_sector_splits projects sector boundaries (same s = same track
position). Corners + the complementary straights PARTITION each lap: the per-segment times
come from one interpolation of the lap's elapsed time at the shared window edges, so the
telescoping sum of all segment times equals the lap time EXACTLY (asserted), and the sum
of all per-segment deltas vs another lap equals the lap-time delta exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import _smooth

# --- model constants -------------------------------------------------------------------
# Curvature smoothing length (metres of arc). The GPS trace is already boxcar-smoothed at
# load (SMOOTH_WINDOW=13 samples); differentiating heading twice still leaves sample-level
# jitter. 8 m ≈ 5 samples at the ~1.5 m spacing of a 10 Hz kart lap — wide enough that the
# straight-line noise floor measures |kappa| ~1e-3 (two decades below the 0.03–0.24 corner
# apexes on the validation track), narrow enough to resolve the shortest real corner arcs
# (>= ~15 m on the validation track).
KAPPA_SMOOTH_M = 8.0
# Median-profile grid step (metres along the reference lap). ~2x finer than the GPS sample
# spacing so the grid never undersamples a lap; finer adds nothing (the data is 10 Hz).
GRID_STEP_M = 0.75
# |kappa| floor before log10 (radius 10 km — far straighter than any steering input; only
# guards log10(0) on exactly-straight samples, it cannot affect the threshold split).
LOG_KAPPA_FLOOR = 1e-4
# Hysteresis: a span EXTENDS while |kappa| >= ratio x threshold — a Schmitt trigger against
# jitter right at the threshold, not a reach into sub-threshold arcs. Measured: the detected
# corner set on both validation recordings is unchanged for any ratio in [0.6, 1.0]
# (12 corners, directions stable, apexes within 6.2/3.5 m worst-case).
HYSTERESIS_RATIO = 0.8
# Adjacent same-direction spans closer than this re-merge (one corner fragmented by jitter,
# not two corners). Below the shortest real inter-corner straight measured on the validation
# track (~16 m between the C8/C9 lefts); the set is unchanged for any gap in [6, 15] m.
MERGE_GAP_M = 10.0
# Minimum corner arc length = the kappa smoothing support: a shorter span is unresolvable
# by construction (the boxcar spread it there). Set is unchanged for [5, 12] m.
MIN_SPAN_M = KAPPA_SMOOTH_M
# Minimum integrated heading change for a real corner. Measured on the validation track:
# every true corner subtends >= 43.7 deg; the sub-corner kinks that must NOT count (e.g. the
# 0060-only ~17 deg flick at 680 m) subtend <= 17 deg. 30 sits mid-gap; the set is unchanged
# for any minimum in [20, 40] deg.
MIN_TURN_DEG = 30.0


@dataclass(frozen=True)
class Corner:
    """One detected corner, in REFERENCE (best) lap odometer metres, track order."""

    cid: int          # 1-based id in track order (C1 is the first corner after the line)
    enter: float      # odometer (m) where sustained cornering starts
    exit: float       # odometer (m) where it ends
    apex: float       # odometer (m) of the |kappa|-weighted centroid (the geometric apex)
    direction: int    # +1 = left (kappa > 0), -1 = right
    turn_deg: float   # integrated heading change magnitude over the span (degrees)

    @property
    def label(self) -> str:
        return f"C{self.cid}"


@dataclass(frozen=True)
class CornerStat:
    """One lap x corner: the projected per-corner metrics (speeds in km/h, times in s)."""

    cid: int                  # Corner.cid this row belongs to
    time: float               # time-in-corner (s)
    delta: float              # time vs the reference lap's same corner (s; 0 for the ref)
    apex_speed: float         # MIN speed inside the window (km/h)
    apex_speed_delta: float   # vs the reference lap's apex speed (km/h; 0 for the ref)
    apex_dist: float          # THIS lap's odometer (m) at the min-speed sample
    entry_speed: float        # speed at the corner-enter boundary (km/h)
    exit_speed: float         # speed at the corner-exit boundary (km/h)


# ------------------------------------------------------------------- curvature profile
def lap_curvature(xs, ys, dists) -> np.ndarray:
    """Signed curvature kappa(s) (1/m, + = left) of one lap's local-frame trace: unwrapped
    heading differentiated vs arc length, boxcar-smoothed over KAPPA_SMOOTH_M of arc.
    `dists` must be strictly increasing (dedupe stationary samples first)."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    dists = np.asarray(dists, float)
    heading = np.unwrap(np.arctan2(np.gradient(ys, dists), np.gradient(xs, dists)))
    kappa = np.gradient(heading, dists)
    ds = float(np.median(np.diff(dists)))
    w = max(int(round(KAPPA_SMOOTH_M / max(ds, 1e-9))), 1)
    return _smooth(kappa, w)


def pooled_curvature(traces, total_ref: float):
    """The track's curvature profile on the reference lap's odometer grid: the MEDIAN of
    the per-lap kappa profiles, aligned by normalized distance (same fraction = same track
    position — the projection identity the whole feature rests on).

    `traces` is an iterable of (xs, ys, cum_dists) triples, one per clean lap (the caller
    passes the session's valid, dropout-free laps; a single trace degrades to that lap's own
    profile). Returns (grid_dists, kappa) with grid_dists spanning [0, total_ref]."""
    n = max(int(round(float(total_ref) / GRID_STEP_M)), 16)
    s_grid = np.linspace(0.0, 1.0, n)
    profiles = []
    for xs, ys, cum in traces:
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        cum = np.asarray(cum, float)
        keep = np.concatenate(([True], np.diff(cum) > 1e-9))  # drop stationary duplicates
        xs, ys, cum = xs[keep], ys[keep], cum[keep]
        if len(cum) < 8 or cum[-1] <= 0:
            continue
        k = lap_curvature(xs, ys, cum)
        if not np.all(np.isfinite(k)):
            continue
        profiles.append(np.interp(s_grid, cum / cum[-1], k))
    if not profiles:
        return s_grid * float(total_ref), np.zeros(n)
    return s_grid * float(total_ref), np.median(np.vstack(profiles), axis=0)


def derive_threshold(kappa) -> float:
    """The corner/straight |kappa| split, derived from THIS track's own distribution — no
    magic constant: Otsu's threshold (maximum between-class variance) on log10 |kappa|.

    WHY the log domain: |kappa| along a real lap is the union of two log-separated modes —
    a straight-line noise floor and a cornering mode. Measured on the two D24 validation
    recordings (median profile over the clean laps): the floor sits at ~1e-3..3e-3 1/m, the
    corner apexes at 0.03..0.24 1/m, with a sparse valley between. In LINEAR space the
    corner mode's long tail dominates Otsu's variance and the split lands unstably inside
    the corner mode itself (measured 0.063 vs 0.050 1/m across the two recordings — a 26%
    swing that cuts gentle sweepers in or out per session). In log space both modes are
    compact, and the split lands mid-valley and stable: measured 0.0068 vs 0.00625 1/m
    (=R~150 m, 9% apart), where real corner entries cross steeply — so span edges barely
    move. Detection is insensitive to the exact value (the corner set on both recordings is
    unchanged when this threshold is scaled by 0.8x..1.25x)."""
    a = np.log10(np.maximum(np.abs(np.asarray(kappa, float)), LOG_KAPPA_FLOOR))
    hist, edges = np.histogram(a, bins=128)
    p = hist.astype(float) / max(hist.sum(), 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)               # class-0 (straighter) weight
    w1 = 1.0 - w0                   # class-1 (cornering) weight
    mu = np.cumsum(p * centers)     # class-0 first moment
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * w0 - mu) ** 2 / (w0 * w1)  # between-class variance per split
    between[~np.isfinite(between)] = 0.0
    return float(10.0 ** centers[int(np.argmax(between))])


# ----------------------------------------------------------------------- detection
def detect_corners(dists, kappa, threshold: float | None = None) -> list[Corner]:
    """Corner list (track order, C1 first) from a curvature profile on an odometer grid.

    Pipeline: hysteresis spans on |kappa| (enter at `threshold`, extend while >= ratio x
    threshold), split at kappa sign changes so an S-complex yields one corner per direction,
    keep only parts that actually reach the threshold, re-merge adjacent same-direction
    parts within MERGE_GAP_M (jitter fragments, not real straights), then drop spans
    shorter than MIN_SPAN_M or turning less than MIN_TURN_DEG (sub-corner kinks).

    A lap is treated LINEARLY [0, total]: the timing line is conventionally on a straight,
    so a corner is not expected to straddle the start/finish seam; if the line does sit in
    an arc, the arc shows as a corner at each end of the lap (consistently across laps)."""
    dists = np.asarray(dists, float)
    kappa = np.asarray(kappa, float)
    if len(dists) < 3:
        return []
    hi = derive_threshold(kappa) if threshold is None else float(threshold)
    lo = hi * HYSTERESIS_RATIO
    a = np.abs(kappa)
    n = len(a)

    # 1. hysteresis spans (index-inclusive [j0, j1]) seeded wherever |kappa| >= hi.
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if a[i] >= hi:
            j0, j1 = i, i
            while j0 > 0 and a[j0 - 1] >= lo:
                j0 -= 1
            while j1 + 1 < n and a[j1 + 1] >= lo:
                j1 += 1
            spans.append((j0, j1))
            i = j1 + 1
        else:
            i += 1

    # 2. split each span at kappa sign changes (S-complex -> one part per direction)…
    parts: list[tuple[int, int]] = []
    for j0, j1 in spans:
        sgn = np.sign(kappa[j0:j1 + 1])
        cuts = np.flatnonzero(np.diff(sgn) != 0)
        lo_i = j0
        for c in cuts:
            parts.append((lo_i, j0 + int(c)))
            lo_i = j0 + int(c) + 1
        parts.append((lo_i, j1))
    # …keeping only parts that genuinely reach the seed threshold (a sign-flip sliver that
    # only ever sat between lo and hi is jitter, not a corner of its own).
    parts = [(j0, j1) for j0, j1 in parts
             if j1 >= j0 and float(np.max(a[j0:j1 + 1])) >= hi]

    def _dir(j0: int, j1: int) -> int:
        seg = kappa[j0:j1 + 1]
        return 1 if seg[int(np.argmax(np.abs(seg)))] > 0 else -1

    # 3. re-merge ADJACENT same-direction parts separated by less than MERGE_GAP_M.
    merged: list[tuple[int, int]] = []
    for p in parts:
        if merged and _dir(*merged[-1]) == _dir(*p) and \
                dists[p[0]] - dists[merged[-1][1]] <= MERGE_GAP_M:
            merged[-1] = (merged[-1][0], p[1])
        else:
            merged.append(p)

    # 4. length + turn-angle filters, apex/direction extraction.
    out: list[Corner] = []
    for j0, j1 in merged:
        dd = dists[j0:j1 + 1]
        if dd[-1] - dd[0] < MIN_SPAN_M:
            continue
        seg = kappa[j0:j1 + 1]
        turn = float(np.degrees(abs(np.trapezoid(seg, dd))))  # integral of kappa ds = angle
        if turn < MIN_TURN_DEG:
            continue
        w = np.abs(seg)
        apex = float(np.sum(dd * w) / np.sum(w))  # |kappa|-weighted centroid (see module doc)
        out.append(Corner(cid=len(out) + 1, enter=float(dd[0]), exit=float(dd[-1]),
                          apex=apex, direction=_dir(j0, j1), turn_deg=turn))
    return out


# ---------------------------------------------------------------------- projection
def _window_edges(corner_list: list[Corner], total_ref: float, total_lap: float) -> np.ndarray:
    """All partition edges (lap odometer metres) for one lap: lap start, each corner's
    enter/exit projected by normalized distance (d_lap = d_ref / total_ref x total_lap —
    the same projection lap_sector_splits uses for sector boundaries), and the lap end."""
    fracs = [0.0]
    for c in corner_list:
        fracs.extend((c.enter / total_ref, c.exit / total_ref))
    fracs.append(1.0)
    return np.asarray(fracs, float) * float(total_lap)


def segment_times(corner_list: list[Corner], total_ref: float, dists, elapsed) -> np.ndarray:
    """Per-segment times (s) of the corner/straight PARTITION of one lap: 2N+1 entries
    [straight0, corner1, straight1, …, cornerN, straightN] (a boundary corner just makes
    its neighbouring straight zero-length). One np.interp of the lap's elapsed time at the
    shared window edges -> the telescoping sum of the segments equals the lap's elapsed
    span EXACTLY (asserted here; the basis of the "sum of segment deltas == lap delta"
    identity the per-corner table is trusted on)."""
    dists = np.asarray(dists, float)
    elapsed = np.asarray(elapsed, float)
    edges = _window_edges(corner_list, total_ref, float(dists[-1]))
    t_at = np.interp(edges, dists, elapsed)
    seg = np.diff(t_at)
    # Partition identity (debug): telescoping — exact up to one float rounding per segment.
    assert abs(float(seg.sum()) - float(elapsed[-1] - elapsed[0])) < 1e-9, \
        "corner/straight partition does not sum to the lap time"
    return seg


def lap_corner_stats(corner_list: list[Corner], total_ref: float, dists, speed_kmh,
                     elapsed, ref: list[CornerStat] | None = None) -> list[CornerStat]:
    """Project the corner windows onto ONE lap and measure each corner: time-in-corner
    (from the same edge interpolation as segment_times, so corner times + straight times
    partition the lap exactly), apex = MIN speed over the in-window samples (+ its lap
    odometer position), entry/exit speeds at the window edges, and deltas vs `ref` (the
    reference — best — lap's own stats; None for the reference lap itself -> deltas 0)."""
    dists = np.asarray(dists, float)
    speed_kmh = np.asarray(speed_kmh, float)
    elapsed = np.asarray(elapsed, float)
    seg = segment_times(corner_list, total_ref, dists, elapsed)
    total_lap = float(dists[-1])
    out: list[CornerStat] = []
    for i, c in enumerate(corner_list):
        d0 = c.enter / total_ref * total_lap
        d1 = c.exit / total_ref * total_lap
        t = float(seg[2 * i + 1])  # this corner's slice of the partition
        idx = np.flatnonzero((dists >= d0) & (dists <= d1))
        if len(idx):
            j = idx[int(np.argmin(speed_kmh[idx]))]
            apex_speed = float(speed_kmh[j])
            apex_dist = float(dists[j])
        else:  # window narrower than the sample spacing — fall back to the midpoint
            apex_dist = (d0 + d1) / 2.0
            apex_speed = float(np.interp(apex_dist, dists, speed_kmh))
        r = ref[i] if ref is not None and i < len(ref) else None
        out.append(CornerStat(
            cid=c.cid, time=t,
            delta=t - r.time if r is not None else 0.0,
            apex_speed=apex_speed,
            apex_speed_delta=apex_speed - r.apex_speed if r is not None else 0.0,
            apex_dist=apex_dist,
            entry_speed=float(np.interp(d0, dists, speed_kmh)),
            exit_speed=float(np.interp(d1, dists, speed_kmh)),
        ))
    return out
