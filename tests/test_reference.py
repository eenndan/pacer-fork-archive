"""Synthetic unit tests for studio.reference — the closed-loop reference-centerline fit.

Pure Python + numpy: no `pacer`, no telemetry file, fast. A regression guard for the cyclic
arc-length correspondence fit: it must recover a known similarity transform (scale,
rotation, translation) from a noisy loop, including the cyclic-start-offset + reversed
traversal + reflection case the old unordered-cloud ICP flunked (it collapsed onto an inner
sub-loop, ~30 % footprint coverage on the real MK sessions).
Run:  python tests/test_reference.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import reference as ref  # noqa: E402


def make_track(n=300):
    """An asymmetric closed loop (egg + bumps) — no rotational symmetry, so the recovered
    rotation is unambiguous."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = 1.0 + 0.35 * np.cos(th) + 0.18 * np.sin(2 * th) + 0.08 * np.cos(3 * th + 0.7)
    return np.column_stack([r * np.cos(th), r * np.sin(th)])


def apply_similarity(xy, scale, ang, t, reflect=False):
    c, s = np.cos(ang), np.sin(ang)
    R = np.array([[c, -s], [s, c]])
    if reflect:
        R = R @ np.array([[1.0, 0.0], [0.0, -1.0]])
    return scale * np.asarray(xy, float) @ R.T + np.asarray(t, float)


def _check_fit(info, scale, rms_tol=4.0):
    assert abs(info["scale"] - scale) / scale < 0.02, info["scale"]
    assert info["rms"] < rms_tol, info["rms"]
    assert info["coverage"] > 0.98, info["coverage"]


def test_recovers_known_transform():
    # The "stored reference" is the track itself; the "lap" is a similarity-transformed,
    # GPS-noisy copy. The fit must recover the transform.
    track = make_track()
    scale, ang, t = 173.0, 0.9, np.array([512.0, -288.0])
    rng = np.random.default_rng(42)
    lap = apply_similarity(track, scale, ang, t) + rng.normal(0, 1.5, (len(track), 2))
    fitted, info = ref.fit_loop_to_loop(track, lap)
    _check_fit(info, scale)
    # Rotation recovered: the fitted R (det +1 here) matches the ground-truth rotation.
    c, s = np.cos(ang), np.sin(ang)
    assert np.linalg.det(info["R"]) > 0
    assert np.allclose(info["R"], [[c, -s], [s, c]], atol=0.02), info["R"]
    assert np.allclose(info["t"], t, atol=4.0), info["t"]
    print("test_recovers_known_transform OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f}")


def test_cyclic_offset_reverse_reflect():
    # The case the old free-scale ICP flunked: the stored loop starts mid-lap, runs the
    # OPPOSITE direction, and is reflected (image y-down vs local y-up). The cyclic search
    # must find the offset+direction and the Umeyama solve must pick the reflection.
    track = make_track()
    stored = np.roll(track, 117, axis=0)[::-1] * np.array([1.0, -1.0])
    rng = np.random.default_rng(7)
    lap = apply_similarity(track, 80.0, -2.2, [-1000.0, 400.0]) \
        + rng.normal(0, 1.0, (len(track), 2))
    fitted, info = ref.fit_loop_to_loop(stored, lap)
    _check_fit(info, 80.0)
    assert np.linalg.det(info["R"]) < 0  # the reflection was recovered
    print("test_cyclic_offset_reverse_reflect OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f} reversed={info['reversed']}")


def test_mk_trace_self_fit():
    # The REAL stored MK polyline (outer ring + infield switchbacks — the exact geometry the
    # old ICP collapsed on): a transformed noisy copy of it must be re-fit near-perfectly.
    norm = ref._load_normalized()
    assert norm is not None and len(norm) >= 30
    truth = ref._resample_closed(norm, 700)
    rng = np.random.default_rng(3)
    lap = np.roll(apply_similarity(truth, 480.0, 2.4, [300.0, 900.0], reflect=True), 250,
                  axis=0) + rng.normal(0, 1.2, (700, 2))
    fitted, info = ref.fit_loop_to_loop(norm, lap)
    _check_fit(info, 480.0)
    print("test_mk_trace_self_fit OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f}")


def test_centerline_local_guards():
    # Degenerate inputs return an empty array (the gap-fill fallback just doesn't exist).
    assert ref.centerline_local(None).shape == (0, 2)
    assert ref.centerline_local(np.zeros((4, 2))).shape == (0, 2)
    print("test_centerline_local_guards OK")


if __name__ == "__main__":
    test_recovers_known_transform()
    test_cyclic_offset_reverse_reflect()
    test_mk_trace_self_fit()
    test_centerline_local_guards()
    print("ALL OK")
