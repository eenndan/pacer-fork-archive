"""Reference track centerline — the fallback gap-fill source.

Used only where no measured lap covers a gap section. The stored normalized best-lap loop is
aligned to the session's own best-lap loop; both are closed loops, so alignment is a closed-loop
cyclic-arc-length similarity fit (details in fit_loop_to_loop).

Pure python + numpy + a stored polyline (no pacer). `centerline_local` takes the session's
best-lap loop and returns the centerline in LOCAL metres as an (M,2) array (or empty).
"""

from __future__ import annotations

import json
import os

import numpy as np

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "mk_centerline.json")

# A lap point within this distance of the fitted reference counts as "covered" — generous
# vs the ~8 m kart-track width + the hand-trace error, tight vs the ~60 m infield spacing,
# so a collapsed/mis-fit reference scores low while a correct fit scores ~100 %.
COVERAGE_TOL_M = 10.0
# Resampled correspondence points for the global cyclic search (offset granularity is
# track_length/N ≈ 2.5 m here) and for the returned polyline.
_N_FIT = 512
_N_OUT = 600


def _load_normalized():
    """The stored centerline as a normalized (M,2) polyline, or None if no data file."""
    if not os.path.exists(_DATA):
        return None
    with open(_DATA) as fh:
        d = json.load(fh)
    pts = np.asarray(d.get("points_norm", []), float)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        return None
    return pts


def _similarity_fit(src, dst):
    """Best-fit similarity (rotation+uniform scale+translation, reflection allowed) mapping
    `src` onto `dst` (both (K,2), point-correspondence assumed). Umeyama closed form."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, s, vt = np.linalg.svd(cov)
    S = np.eye(2)
    R = u @ S @ vt
    var_s = (xs ** 2).sum() / len(src)
    scale = (s * np.diag(S)).sum() / var_s if var_s > 0 else 1.0
    t = mu_d - scale * R @ mu_s
    return scale, R, t


def _resample_closed(xy, n):
    """Resample a CLOSED loop uniformly by arc length to n points (no duplicate endpoint).

    The loop is closed before measuring (the final segment back to the start counts), so a
    polyline whose last point isn't a repeat of the first still parameterizes the full ring.
    """
    xy = np.asarray(xy, float)
    if np.hypot(*(xy[-1] - xy[0])) > 1e-12:
        xy = np.vstack([xy, xy[:1]])
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    if d[-1] <= 0:
        return np.repeat(xy[:1], n, axis=0)
    s = np.arange(n) * (d[-1] / n)
    return np.column_stack([np.interp(s, d, xy[:, 0]), np.interp(s, d, xy[:, 1])])


def _dist_to_polyline(pts, poly):
    """Min Euclidean distance from each of `pts` (P,2) to the polyline `poly` (Q,2) —
    true point-to-SEGMENT distance, vectorized over all P×(Q-1) pairs."""
    pts = np.asarray(pts, float)
    a, b = poly[:-1], poly[1:]
    ab = b - a                                            # (S,2)
    ab2 = np.maximum((ab ** 2).sum(1), 1e-12)             # (S,)
    ap = pts[:, None, :] - a[None]                        # (P,S,2)
    t = np.clip((ap * ab[None]).sum(-1) / ab2[None], 0.0, 1.0)
    closest = a[None] + t[..., None] * ab[None]
    return np.sqrt(((pts[:, None, :] - closest) ** 2).sum(-1)).min(1)


def _close_ring(xy):
    """Append the first point so the returned polyline draws as a closed ring."""
    return np.vstack([xy, xy[:1]])


def fit_loop_to_loop(ref_xy, loop_xy, n=_N_FIT, icp_iters=8):
    """Fit the closed reference loop `ref_xy` onto the closed measured loop `loop_xy` (both
    (K,2), any scale/frame) by a similarity transform.

    Global search over cyclic offset × direction (scored by the closed-form similarity residual),
    then a nearest-point ICP polish — but every candidate is accepted only by the reported
    lap->ref RMS, so the polish can't trade footprint coverage for nearest-point comfort.

    Returns `(fitted, info)`: `fitted` is the reference as a closed ring in the measured frame;
    `info` has `rms` (m), `coverage`, `scale`, `R`, `t`, `offset_frac`, `reversed`.
    """
    ref_xy = np.asarray(ref_xy, float)
    loop_xy = np.asarray(loop_xy, float)
    ref_n = _resample_closed(ref_xy, n)
    lap_n = _resample_closed(loop_xy, n)

    best = None  # (residual_rms, scale, R, t, offset, reversed)
    for rev in (False, True):
        cand = ref_n[::-1] if rev else ref_n
        for k in range(n):
            src = np.roll(cand, -k, axis=0)
            scale, R, t = _similarity_fit(src, lap_n)
            res = (scale * src @ R.T + t) - lap_n
            rms = float(np.sqrt((res ** 2).sum(1).mean()))
            if best is None or rms < best[0]:
                best = (rms, scale, R, t, k, rev)
    _, scale, R, t, k, rev = best

    # The transform was solved on the rolled/reversed resampling; it applies to the
    # reference as a SET, so carry it over to the canonical resampled reference directly.
    ref_out = _resample_closed(ref_xy, _N_OUT)
    dense = _resample_closed(loop_xy, max(4 * n, 2048))

    def _apply(sc, rot, tr):
        return sc * ref_out @ rot.T + tr

    def _score(fitted):
        d = _dist_to_polyline(loop_xy, _close_ring(fitted))
        return float(np.sqrt((d ** 2).mean())), float((d <= COVERAGE_TOL_M).mean())

    fitted = _apply(scale, R, t)
    rms, cov = _score(fitted)
    win = (rms, cov, scale, R, t, fitted)

    # --- ICP polish, accepted only by the reported lap→reference metric ---
    cur = fitted
    for _ in range(icp_iters):
        d2 = ((cur[:, None, 0] - dense[None, :, 0]) ** 2
              + (cur[:, None, 1] - dense[None, :, 1]) ** 2)
        nn = dense[np.argmin(d2, axis=1)]
        sc_i, R_i, t_i = _similarity_fit(ref_out, nn)
        cur = _apply(sc_i, R_i, t_i)
        rms_i, cov_i = _score(cur)
        if rms_i < win[0]:
            win = (rms_i, cov_i, sc_i, R_i, t_i, cur)

    rms, cov, scale, R, t, fitted = win
    info = {"rms": rms, "coverage": cov, "scale": float(scale), "R": R,
            "t": np.asarray(t, float), "offset_frac": k / n, "reversed": rev}
    return _close_ring(fitted), info


def centerline_local(loop_xy):
    """Return the reference centerline in LOCAL metres as an (M,2) closed ring — empty if
    unavailable. `loop_xy` is the session's BEST-LAP loop (ordered local-metre points): a
    closed curve, so the stored loop is aligned to it by cyclic arc-length correspondence
    (see `fit_loop_to_loop`). Prints the winning fit RMS + footprint coverage."""
    norm = _load_normalized()
    if norm is None or loop_xy is None or len(loop_xy) < 10:
        return np.empty((0, 2))
    fitted, info = fit_loop_to_loop(norm, loop_xy)
    print(f"[reference] MK centerline fit: RMS {info['rms']:.1f} m, "
          f"{info['coverage']:.0%} of best-lap points within {COVERAGE_TOL_M:.0f} m",
          flush=True)
    return fitted
