"""CornerModel — the per-segmentation CORNER analysis cluster, extracted from Session.

What lives here (and ONLY here): the detected corner list + its reference total (the
`corners.py`-backed basis), the per-lap projected corner stats (incl. the cross-recording
reference's own projected stats under REFERENCE_ID), and the per-corner session-best times.
All three are derived from the current segmentation, so all three live behind ONE service
that Session composes + delegates to — the same template as render_cache.LapRenderCache.

PACER-FREE by design (PLAN.md: only ingest/load/session/tracks may import pacer). Every
input arrives through Session-bound callables over Session's own cached per-lap primitives
(`_lap_columns` / `_lap_arrays`) and its memoized lap sets (`best_lap_id` / `valid_lap_ids`),
so this module never touches the bound core — exactly like LapRenderCache.

Cache lifetime — the invalidation invariant (see Session.set_timing_lines): the corner set is
detected on the best lap's grid and projected per lap, so it goes stale on re-segmentation
(lap ids / times / the best lap all shift when a timing line moves). Session calls
`invalidate()` from set_timing_lines, which drops all three caches together — exactly the
hand-clearing block this replaces. A SEPARATE, narrower drop, `invalidate_stats()`, clears
only the per-lap stats (NOT the corner detection): the per-corner Δ baseline changes when a
cross-recording reference is set/cleared, so Session calls it from set_reference_session /
clear_reference (the detection windows are unchanged there, only the deltas-vs-baseline).
"""

from __future__ import annotations

import numpy as np

from . import corners

# Local "cache not yet computed" sentinel (None is a legal cached value for basis/bests) — the
# SAME idiom Session uses; kept module-local so corner_model never imports back from session
# (PLAN's no-cycle rule). The per-lap stats cache parks the cross-recording reference's OWN
# projected stats under the reference sentinel KEY Session passes in (its REFERENCE_ID).
_UNSET = object()


class CornerModel:
    """Corner detection + per-corner per-lap stats over Session-provided primitives.

    `session` is the owning Session, read back for its per-lap caches (`_lap_columns` /
    `_lap_arrays`), its memoized lap sets (`best_lap_id` / `valid_lap_ids` / `lap_has_dropout`)
    and the active cross-recording reference (`_ref`). The back-reference mirrors how the delta
    seam already reaches Session's primitives — the corner math is just numpy on those arrays.
    `reference_id` is Session.REFERENCE_ID, the sentinel the reference stats are parked under.
    """

    def __init__(self, session, reference_id: int):
        self._s = session
        self._reference_id = reference_id
        # The detected corner list + its reference total ((corners, total_ref) tuple). _UNSET
        # sentinel: None is a legal "no usable best lap" result, distinct from "not yet computed".
        self._basis_cache: object = _UNSET
        # Per-lap projected corner stats (+ the reference's own under reference_id). Cleared on
        # re-segment (invalidate) AND on a reference change (invalidate_stats — only the baseline
        # moved, not the windows).
        self._stats_cache: dict[int, list[corners.CornerStat]] = {}
        # Per-corner session-best time-in-corner. _UNSET sentinel ([] is a legal "no corners").
        self._bests_cache: object = _UNSET

    def invalidate(self) -> None:
        """Drop EVERY corner cache — called from Session.set_timing_lines (the single
        re-segmentation point): the corner set is detected on + projected through the
        segmentation, so all three are stale after a timing-line change."""
        self._basis_cache = _UNSET
        self._stats_cache.clear()
        self._bests_cache = _UNSET

    def invalidate_stats(self) -> None:
        """Drop ONLY the per-lap stats (not the corner detection) — called from
        Session.set_reference_session / clear_reference: the per-corner Δ baseline switched
        (best lap <-> reference lap), so every cached per-lap stat delta is stale, but the
        corner windows themselves are unchanged. Recomputed lazily against the new baseline."""
        self._stats_cache.clear()

    # ------------------------------------------------------------------ basis + corners
    def basis(self) -> tuple[list[corners.Corner], float] | None:
        """The cached (corner list, reference total distance) pair, or None when there is no
        usable best lap. The reference total is the best lap's odometer length — the basis
        the corner windows (and the delta plot's distance axis) are expressed in."""
        if self._basis_cache is not _UNSET:
            return self._basis_cache
        s = self._s
        self._basis_cache = None
        best = s.best_lap_id()
        if best is not None:
            _t, _xs, _ys, _v, cum_best = s._lap_columns(best)
            if len(cum_best) >= 8 and float(cum_best[-1]) > 0:
                total_ref = float(cum_best[-1])
                # The median curvature profile pools the session's clean laps (valid, no GPS
                # dropout); the best lap is always included so a session where every lap is
                # dropout-flagged still detects on the best lap alone.
                ids = [i for i in s.valid_lap_ids() if not s.lap_has_dropout(i)]
                if best not in ids:
                    ids.append(best)
                traces = []
                for lid in ids:
                    _lt, xs, ys, _lv, cum = s._lap_columns(lid)
                    traces.append((xs, ys, cum))
                d_grid, kappa = corners.pooled_curvature(traces, total_ref)
                self._basis_cache = (corners.detect_corners(d_grid, kappa), total_ref)
        return self._basis_cache

    def corner_list(self) -> list[corners.Corner]:
        """The detected corners (C1… in track order) in best-lap odometer metres. [] when
        no best lap exists. Computed once per segmentation (see basis)."""
        basis = self.basis()
        return basis[0] if basis is not None else []

    # ------------------------------------------------------------------ per-lap stats
    def reference_corner_stats(self) -> list[corners.CornerStat] | None:
        """The cross-recording reference lap's per-corner stats projected onto THIS session's
        corner windows (the same normalized-distance projection any local lap uses), or None
        when no reference is loaded. Cached under the reference sentinel key; invalidated when
        the reference or the segmentation changes (invalidate_stats / invalidate)."""
        ref = self._s._ref
        if ref is None:
            return None
        got = self._stats_cache.get(self._reference_id)
        if got is not None:
            return got
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = ref.arrays()
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return None
        # ref=None on the reference itself -> its own deltas are 0 (it IS the baseline).
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=None)
        self._stats_cache[self._reference_id] = stats
        return stats

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the baseline's same corner). [] for a degenerate lap or when no corners were detected.
        Cached per lap; cleared on re-segment (and on a reference change via invalidate_stats).

        The Δ baseline is the local best lap normally, or the CROSS-RECORDING reference lap's
        projected corner stats when one is loaded (F7)."""
        got = self._stats_cache.get(lap_id)
        if got is not None:
            return got
        s = self._s
        basis = self.basis()
        best = s.best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = s._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        # The reference stats are the baseline lap's own (deltas measured against them): the
        # cross-recording reference when loaded, else the local best lap (whose self-deltas are
        # 0). Computed first, then this lap's deltas are measured against them.
        ref_stats = self.reference_corner_stats()
        if ref_stats is not None:
            ref = ref_stats
        else:
            ref = self.lap_corner_stats(best) if lap_id != best else None
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None)
        self._stats_cache[lap_id] = stats
        return stats

    def corner_session_bests(self) -> list[float]:
        """Per-corner session-best time-in-corner across all VALID laps (the purple-cell
        convention, matching the per-sector session bests). [] when no corners. Cached;
        cleared on re-segment."""
        if self._bests_cache is not _UNSET:
            return self._bests_cache
        s = self._s
        per_lap = [self.lap_corner_stats(i) for i in s.valid_lap_ids()]
        per_lap = [st for st in per_lap if st]
        n = len(self.corner_list())
        self._bests_cache = [
            min(st[i].time for st in per_lap) for i in range(n)
        ] if per_lap and n else []
        return self._bests_cache

    # ------------------------------------------------------------------ map / seek glue
    def corner_map_markers(self) -> list[tuple[str, float, float, int]]:
        """(label, x, y, direction) per corner — the apex position in LOCAL metres on the
        best lap's trace, for the map's corner labels. [] when no corners/best lap."""
        s = self._s
        basis = self.basis()
        best = s.best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, _total_ref = basis
        _t, xs, ys, _v, cum = s._lap_columns(best)
        apexes = np.asarray([c.apex for c in corner_list])
        mx = np.interp(apexes, cum, xs)
        my = np.interp(apexes, cum, ys)
        return [(c.label, float(mx[i]), float(my[i]), c.direction)
                for i, c in enumerate(corner_list)]

    def corner_entry_media_time(self, lap_id: int, cid: int) -> float | None:
        """The media-clock time (s) at which `lap_id` ENTERS corner `cid` — the jump-to seek
        target for an opportunity. The corner's reference-odometer enter point is projected
        onto this lap by normalized distance (the SAME projection lap_corner_stats uses), then
        the lap's elapsed→media time is read at that distance. None when the corner/lap is
        unknown or degenerate. Returned as an ABSOLUTE media time (lap start + elapsed)."""
        s = self._s
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        corner = next((c for c in corner_list if c.cid == cid), None)
        if corner is None:
            return None
        td = s._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return None
        d_enter = corner.enter / total_ref * total_lap  # project onto THIS lap's odometer
        return float(np.interp(d_enter, dists, times))
