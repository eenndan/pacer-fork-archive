"""Regression pins for the F1 god-object decomposition: Session composes the per-domain
analysis SERVICES (studio.corner_model.CornerModel + studio.driving_channels.DrivingChannels)
and DELEGATES to them, and `set_timing_lines` / the reference set+clear drive the services'
`invalidate()` seam instead of hand-clearing ~7 lap-keyed dict/sentinel slots.

The bar the F1 refactor was held to is WHOLE-API numerical equivalence on the real session
(proved out-of-band by dev/golden_session_dump.py — 122k float values, max |Δ| = 0). This
file pins the STRUCTURAL contracts that keep that true going forward, on a deterministic
SYNTHETIC session (the test_corners stadium loop + a seeded g-meter — no pacer Laps, no
telemetry file), so a regression that re-introduces a stale-cache or over-clearing bug fails
here:

  1. DELEGATION — Session's public corner/driving accessors return EXACTLY what the composed
     service returns (same object once cached): the thin delegators forward, they don't
     recompute a second copy.
  2. CACHING — the services memoize per lap (a second call returns the SAME object).
  3. invalidate() ACTUALLY CLEARS — each service's cache dicts/sentinels are emptied by its
     own invalidate(); CornerModel.invalidate_stats() clears ONLY the per-lap stats and KEEPS
     the detected-corner basis (the narrower drop the reference change needs).
  4. set_timing_lines DRIVES BOTH invalidate()s — after a re-segment every per-lap service
     cache is empty; the driving THRESHOLDS survive (they depend only on the constant g series).
  5. THE REFERENCE SEAM uses invalidate_stats() — set_reference_session / clear_reference drop
     the per-lap corner stats (the Δ baseline moved) but NOT the corner detection windows.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_session_services.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import corner_model, driving_channels, gmeter  # noqa: E402

# Each service carries its OWN module-local "not yet computed" sentinel (corner_model._UNSET /
# driving_channels._UNSET — they never import back from session, so they don't share its
# sentinel). The cleared-slot assertions below compare against the SERVICE's sentinel.
_CM_UNSET = corner_model._UNSET

from _synthetic import bare_session, reset_corner_caches, reset_driving_caches  # noqa: E402
from test_corners import elapsed_for, speed_profile, stadium  # noqa: E402


def _synthetic_session():
    """A bare Session with two clean stadium laps (REAL corner detection) + a seeded g-meter
    that brakes through the corners, so EVERY corner + driving channel produces real output.
    Built through the F1 service-aware reset helpers — the post-extraction seeding idiom."""
    s = bare_session(valid=[0, 1], best=0)
    s._cols_cache = {}
    xs, ys, cum = stadium()
    sp_a = speed_profile(cum, 0.7)
    sp_b = speed_profile(cum, 2.1)
    t_a = 100.0 + elapsed_for(cum, sp_a)
    t_b = 300.0 + elapsed_for(cum, sp_b)
    s._cols_cache[0] = (t_a, xs, ys, sp_a, cum)
    s._cols_cache[1] = (t_b, xs, ys, sp_b, cum)
    s._dist_cache = {0: (t_a, cum.copy(), t_a - t_a[0]),
                     1: (t_b, cum.copy(), t_b - t_b[0])}
    # The other Session-internal caches set_timing_lines clears (the bare-Session path lacks them
    # since no __init__ ran); a no-op render cache so its invalidate() is reachable too.
    s._xyt_cache = {}
    # A no-op render cache: invalidate() (set_timing_lines) + reference_fit_loop() (the reference
    # adopt path) are the only methods these tests reach; the spatial overlay fit is irrelevant
    # to the cache-clearing assertions, so the fit loop is None (overlay simply not drawn).
    s._render_cache = SimpleNamespace(invalidate=lambda: None, reference_fit_loop=lambda: None)
    # A g-meter over both laps' span (its own clock), braking hard through the two arcs so the
    # brake/coast/grip channels and the derived thresholds are all non-trivial.
    gt = np.arange(99.0, t_b[-1] + 1.0, 0.02)
    long_g = -0.6 * (np.sin(gt * 0.5) ** 2)   # repeated decel pulses
    lat_g = 0.8 * np.cos(gt * 0.3)
    s._gmeter = gmeter.GMeter(times=gt, lat_g=lat_g, long_g=long_g, cross=None, source="accl")
    s.tt = gt.copy()
    s.tv = np.full(len(gt), 50.0)  # km/h on the g clock (for derive_thresholds' speed gate)
    reset_corner_caches(s)
    reset_driving_caches(s)
    return s


# --------------------------------------------------------------------- composition + delegation
def test_session_composes_the_services():
    """Session lazily owns a CornerModel + a DrivingChannels (the bare-Session path builds them
    on first `_cm`/`_dc` access — the same getattr idiom `_ref` uses), each a real service."""
    s = _synthetic_session()
    assert isinstance(s._cm, corner_model.CornerModel)
    assert isinstance(s._dc, driving_channels.DrivingChannels)
    # The accessor is stable (same instance each call — not rebuilt per access).
    assert s._cm is s._cm and s._dc is s._dc
    print("ok compose: Session._cm/_dc are the composed services")


def test_corner_delegation_is_exact():
    """Every public corner accessor returns EXACTLY the composed CornerModel's value."""
    s = _synthetic_session()
    assert s.corners() == s._cm.corner_list()
    assert len(s.corners()) >= 2, "the stadium must detect its two arcs as corners"
    # lap_corner_stats: delegates AND caches (same object back the second time, both via Session
    # and the service — they share the one cache).
    st = s.lap_corner_stats(0)
    assert st is s._cm.lap_corner_stats(0), "delegation must hit the SAME cached object"
    assert s.lap_corner_stats(0) is st, "per-lap stats must cache"
    assert s.corner_session_bests() == s._cm.corner_session_bests()
    assert s.corner_map_markers() == s._cm.corner_map_markers()
    assert s._corner_basis() is s._cm.basis()
    # corner_entry_media_time delegates too.
    cid = s.corners()[0].cid
    assert s.corner_entry_media_time(0, cid) == s._cm.corner_entry_media_time(0, cid)
    print(f"ok corner delegation: {len(s.corners())} corners, stats/bests/markers all delegate")


def test_driving_delegation_is_exact():
    """Every public driving accessor returns EXACTLY the composed DrivingChannels' value."""
    s = _synthetic_session()
    th = s.driving_thresholds()
    assert th is not None and th is s._dc.thresholds(), "thresholds delegate + cache"
    be = s.lap_brake_events(0)
    assert be is s._dc.lap_brake_events(0), "delegation must hit the SAME cached object"
    assert s.lap_brake_events(0) is be, "brake events must cache per lap"
    assert s.lap_coasting_spans(0) is s._dc.lap_coasting_spans(0)
    assert s.lap_corner_grip(0) == s._dc.lap_corner_grip(0)
    assert s.lap_brake_map_markers(0) == s._dc.lap_brake_map_markers(0)
    for mode in ("distance", "time"):
        assert s.lap_brake_plot_positions(0, mode) == s._dc.lap_brake_plot_positions(0, mode)
        assert s.lap_coasting_plot_spans(0, mode) == s._dc.lap_coasting_plot_spans(0, mode)
    print(f"ok driving delegation: theta_b={th.theta_b:.3f}, "
          f"{len(be)} brake event(s), all channels delegate")


# ------------------------------------------------------------------ invalidate() actually clears
def test_corner_invalidate_clears_its_caches():
    """CornerModel.invalidate() empties ALL three caches; invalidate_stats() empties ONLY the
    per-lap stats and KEEPS the detected-corner basis."""
    s = _synthetic_session()
    s.lap_corner_stats(0)          # populate the per-lap stats + the basis
    s.corner_session_bests()       # populate the session bests
    cm = s._cm
    assert cm._basis_cache is not _CM_UNSET and cm._stats_cache and cm._bests_cache is not _CM_UNSET

    # invalidate_stats: stats gone, basis + bests KEPT (the narrower reference-change drop).
    basis_before = cm._basis_cache
    cm.invalidate_stats()
    assert not cm._stats_cache, "invalidate_stats must clear the per-lap stats"
    assert cm._basis_cache is basis_before, "invalidate_stats must KEEP the corner basis"

    # full invalidate: everything reset to the not-computed sentinels.
    s.lap_corner_stats(0)
    cm.invalidate()
    assert cm._basis_cache is _CM_UNSET and not cm._stats_cache and cm._bests_cache is _CM_UNSET
    print("ok corner invalidate: invalidate() clears all; invalidate_stats() keeps the basis")


def test_driving_invalidate_keeps_thresholds():
    """DrivingChannels.invalidate() empties the three per-lap caches but KEEPS the derived
    thresholds (they depend only on the constant-for-the-recording g series)."""
    s = _synthetic_session()
    s.lap_brake_events(0)
    s.lap_coasting_spans(0)
    s.lap_corner_grip(0)
    dc = s._dc
    th = dc.thresholds()
    assert th is not None and dc._brake_events_cache and dc._coasting_spans_cache
    dc.invalidate()
    assert not dc._brake_events_cache and not dc._coasting_spans_cache and not dc._corner_grip_cache
    assert dc.thresholds() is th, "invalidate() must KEEP the (g-only) thresholds"
    print("ok driving invalidate: per-lap caches cleared, thresholds kept")


# ------------------------------------------------------ set_timing_lines drives both invalidate()s
def test_set_timing_lines_invalidates_both_services():
    """set_timing_lines (the single re-segmentation point) drives BOTH services' invalidate():
    after a re-segment every per-lap service cache is empty (recomputed lazily), the corner
    basis is reset, and the driving thresholds survive."""
    s = _synthetic_session()
    s.lap_corner_stats(0)
    s.lap_brake_events(0)
    s.lap_corner_grip(0)
    s.corner_session_bests()
    th = s.driving_thresholds()
    cm, dc = s._cm, s._dc
    assert cm._stats_cache and dc._brake_events_cache and cm._basis_cache is not _CM_UNSET

    # A FakeLaps whose update() is a no-op + an empty sectors so set_timing_lines runs without a
    # pacer Laps. Seg endpoints are irrelevant to the cache-clearing assertion.
    s.laps = SimpleNamespace(
        sectors=SimpleNamespace(sector_lines=[]),
        update=lambda: None,
    )
    from studio.session import Seg
    seg = Seg(0.0, 0.0, 0.0, 1.0)
    # Stub the pacer.Sectors construction set_timing_lines does (it assigns s.laps.sectors).
    import studio.session as session_mod
    real_sectors = session_mod.pacer.Sectors
    session_mod.pacer.Sectors = lambda **kw: SimpleNamespace(sector_lines=[])
    try:
        s.set_timing_lines(seg, [])
    finally:
        session_mod.pacer.Sectors = real_sectors

    assert not cm._stats_cache, "corner stats must clear on re-segment"
    assert cm._basis_cache is _CM_UNSET, "corner basis must clear on re-segment"
    assert cm._bests_cache is _CM_UNSET, "corner bests must clear on re-segment"
    assert not dc._brake_events_cache and not dc._coasting_spans_cache and not dc._corner_grip_cache
    assert dc.thresholds() is th, "the driving thresholds survive a re-segment"
    print("ok set_timing_lines: both services invalidated; driving thresholds survive")


# ----------------------------------------------------------- reference set/clear uses stats-only drop
def test_reference_change_drops_only_per_lap_stats():
    """set_reference_session + clear_reference drive CornerModel.invalidate_stats(): the per-lap
    corner-stat deltas (measured against the now-different baseline) are dropped, but the corner
    DETECTION basis is preserved (the windows don't move when only the Δ baseline does)."""
    s = _synthetic_session()
    s.track_name = "Stadium"
    s.lap_corner_stats(0)
    cm = s._cm
    basis_before = cm.basis()
    assert cm._stats_cache and basis_before is not None

    # A data-only reference (a second synthetic session, same track) — set_reference_session
    # adopts it and must drop ONLY the per-lap stats.
    ref = _synthetic_session()
    ref.track_name = "Stadium"
    reason = s.set_reference_session(ref, source_label="ref")
    assert reason is None, reason
    assert s.has_reference()
    assert not cm._stats_cache, "the per-lap stats must drop when the reference baseline changes"
    assert cm._basis_cache is not _CM_UNSET, "the corner detection basis must be KEPT"
    assert cm.basis() is basis_before, "same detected corners after a reference change"

    # clear_reference reverts the baseline -> drops the per-lap stats again, keeps the basis.
    s.lap_corner_stats(0)
    assert cm._stats_cache
    s.clear_reference()
    assert not s.has_reference()
    assert not cm._stats_cache, "the per-lap stats must drop again on clear_reference"
    assert cm._basis_cache is not _CM_UNSET, "the corner detection basis must survive clear_reference"
    print("ok reference seam: set/clear drop per-lap stats only; corner basis preserved")


if __name__ == "__main__":
    tests = [
        test_session_composes_the_services,
        test_corner_delegation_is_exact,
        test_driving_delegation_is_exact,
        test_corner_invalidate_clears_its_caches,
        test_driving_invalidate_keeps_thresholds,
        test_set_timing_lines_invalidates_both_services,
        test_reference_change_drops_only_per_lap_stats,
    ]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SESSION-SERVICE TESTS PASSED")
