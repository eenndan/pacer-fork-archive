"""DrivingChannels — the driving-channel analysis cluster (F5), extracted from Session.

What lives here (and ONLY here): the brake/coast thresholds derived ONCE from this session's
own g distribution, plus the per-lap brake events, coasting spans, and per-corner grip
utilization (the `driving.py`-backed channels + their caches). Same template as
render_cache.LapRenderCache / corner_model.CornerModel: Session composes this + delegates.

PACER-FREE by design (PLAN.md). The g series + the per-lap arrays all arrive through the
owning Session's existing primitives (`_gmeter` / `tt` / `tv` / `_lap_time_dist_elapsed` /
`_lap_arrays` / `_lap_columns`), and the corner windows for grip come from the sibling
CornerModel service — so this module never touches the bound core.

Cache lifetime — the invalidation invariant (see Session.set_timing_lines): the derived
thresholds depend only on the (constant-for-the-recording) g series, so they are NOT dropped
on re-segment — only the per-lap results are, since they are projected through the
segmentation. `invalidate()` clears exactly the three per-lap caches (brake / coast / grip),
keeping the thresholds — exactly the hand-clearing block this replaces.
"""

from __future__ import annotations

import numpy as np

from . import driving

# Local "cache not yet computed" sentinel (None is a legal cached value for the thresholds) —
# the SAME idiom Session uses; kept module-local so this never imports back from session.
_UNSET = object()


class DrivingChannels:
    """Brake / coast / grip channels + the session-wide thresholds, over Session primitives.

    `session` is the owning Session, read back for the g series (`_gmeter`), the full-trace
    clock + speed (`tt` / `tv`), the per-lap arrays (`_lap_time_dist_elapsed` / `_lap_arrays`),
    the active baseline distance + best lap for the plot-position scaling, and the sibling
    CornerModel's basis (for the per-corner grip windows).
    """

    def __init__(self, session):
        self._s = session
        # The brake/coast thresholds derived ONCE from this session's own g distribution. _UNSET
        # sentinel: None is a legal "no g signal" result. Kept across re-segments (the g series
        # is constant); see invalidate().
        self._thresholds_cache: object = _UNSET
        # Per-lap channels, all projected through the segmentation -> cleared on re-segment.
        self._brake_events_cache: dict[int, list[driving.BrakeEvent]] = {}
        self._coasting_spans_cache: dict[int, list[driving.CoastSpan]] = {}
        self._corner_grip_cache: dict[int, list[float]] = {}

    def invalidate(self) -> None:
        """Drop the per-lap channels — called from Session.set_timing_lines (the single
        re-segmentation point): brake events / coasting spans / per-corner grip are all
        projected through the segmentation, so they are stale after a timing-line change. The
        derived thresholds depend only on the (unchanged) g series, so they are kept."""
        self._brake_events_cache.clear()
        self._coasting_spans_cache.clear()
        self._corner_grip_cache.clear()

    # ------------------------------------------------------------------ g + thresholds
    def _lap_g_arrays(self, lap_id: int):
        """(long_g, lat_g) for a lap, aligned 1:1 to the lap's cached (dist, elapsed) arrays.

        The g series (Session._gmeter) lives on the MEDIA clock — the SAME clock the lap's own
        per-point `times` come from — so the per-lap g is just the meter's long/lat g
        interpolated at the lap's media times. Returns (None, None) when there's no g signal or
        the lap is degenerate, so the callers degrade to empty channels."""
        s = self._s
        gm = s._gmeter
        if not gm.has_data:
            return None, None
        td = s._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None, None
        times, _dists, _elapsed = td
        long_g = np.interp(times, gm.times, gm.long_g)
        lat_g = np.interp(times, gm.times, gm.lat_g)
        return long_g, lat_g

    def thresholds(self):
        """The brake/coast thresholds (driving.Thresholds) derived ONCE from this session's own
        full-session g distribution over its moving samples — no magic constants. None when
        there's no g signal. Cached for the recording (the g series is constant). Built from the
        WHOLE g series + the matching speed (resampled to the g clock)."""
        if self._thresholds_cache is not _UNSET:
            return self._thresholds_cache
        s = self._s
        gm = s._gmeter
        if not gm.has_data:
            self._thresholds_cache = None
            return None
        # Speed (km/h) on the g clock: the full-trace speed tv (km/h) interpolated onto gm.times
        # (the trace + the g series share the media clock).
        speed_kmh = np.interp(gm.times, s.tt, s.tv)
        self._thresholds_cache = driving.derive_thresholds(
            gm.long_g, gm.lat_g, speed_kmh)
        return self._thresholds_cache

    # ------------------------------------------------------------------ per-lap channels
    def lap_brake_events(self, lap_id: int) -> list[driving.BrakeEvent]:
        """Braking zones detected on one lap's longitudinal-g series (onset odometer/time,
        peak decel, duration), in track order. [] when there's no g signal or the lap is
        degenerate. Cached per lap; cleared on re-segment."""
        got = self._brake_events_cache.get(lap_id)
        if got is not None:
            return got
        th = self.thresholds()
        long_g, _lat_g = self._lap_g_arrays(lap_id)
        td = self._s._lap_time_dist_elapsed(lap_id)
        if th is None or long_g is None or td is None:
            return []
        _times, dists, elapsed = td
        events = driving.brake_events(dists, elapsed, long_g, th.theta_b)
        self._brake_events_cache[lap_id] = events
        return events

    def lap_coasting_spans(self, lap_id: int) -> list[driving.CoastSpan]:
        """Coasting spans (neither braking/accelerating nor cornering) on one lap, in track
        order. [] when there's no g signal or the lap is degenerate. Cached per lap; cleared
        on re-segment."""
        got = self._coasting_spans_cache.get(lap_id)
        if got is not None:
            return got
        s = self._s
        th = self.thresholds()
        long_g, lat_g = self._lap_g_arrays(lap_id)
        td = s._lap_time_dist_elapsed(lap_id)
        if th is None or long_g is None or td is None:
            return []
        _times, dists, _elapsed = td
        _d, speed_kmh, elapsed = s._lap_arrays(lap_id)
        spans = driving.coasting_spans(dists, elapsed, speed_kmh[:len(dists)], long_g, lat_g,
                                       th.theta_c, th.theta_lat)
        self._coasting_spans_cache[lap_id] = spans
        return spans

    def lap_corner_grip(self, lap_id: int) -> list[float]:
        """Per-corner friction-circle grip utilization for one lap (median |g| / lap-envelope
        max inside each corner window, in (0,1]), one value per detected corner in track order.
        [] when there's no g signal, no corners, or the lap is degenerate. Cached per lap;
        cleared on re-segment."""
        got = self._corner_grip_cache.get(lap_id)
        if got is not None:
            return got
        s = self._s
        long_g, lat_g = self._lap_g_arrays(lap_id)
        basis = s._corner_basis()
        if long_g is None or basis is None or not basis[0]:
            return []
        corner_list, total_ref = basis
        td = s._lap_time_dist_elapsed(lap_id)
        if td is None:
            return []
        _times, dists, _elapsed = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return []
        # Project each corner's reference-odometer window onto this lap by normalized distance
        # (d_lap = d_ref / total_ref * total_lap) — the SAME projection lap_corner_stats uses.
        windows = [(c.enter / total_ref * total_lap, c.exit / total_ref * total_lap)
                   for c in corner_list]
        grip = driving.corner_grip(dists, long_g, lat_g, windows)
        self._corner_grip_cache[lap_id] = grip
        return grip

    # ------------------------------------------------------------------ map / plot glue
    def lap_brake_map_markers(self, lap_id: int) -> list[tuple[float, float, float]]:
        """(x, y, peak_decel) per brake onset on one lap, in LOCAL metres on that lap's own
        trace — for the map's brake glyphs. [] when no brake events. The onset odometer is
        mapped to the lap's (x, y) via the lap's cached columns."""
        s = self._s
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        _t, xs, ys, _v, cum = s._lap_columns(lap_id)
        onsets = np.asarray([e.onset_dist for e in events])
        mx = np.interp(onsets, cum, xs)
        my = np.interp(onsets, cum, ys)
        return [(float(mx[i]), float(my[i]), e.peak_decel) for i, e in enumerate(events)]

    def lap_brake_plot_positions(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x, peak_decel) per brake onset on one lap, on the speed chart's SHARED axis
        for `mode` ('distance' or 'time'). [] when no brake events / no best lap (distance mode).
          * 'distance': x = (onset_dist / lap_total) * baseline_distance
          * 'time':     x = onset_time (elapsed into the lap)"""
        s = self._s
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        if mode == "time":
            return [(e.onset_time, e.peak_decel) for e in events]
        # 'distance' — normalize by this lap's total, scale to the ACTIVE baseline's distance
        # (the reference total when one is loaded) so the glyphs sit on the curves/cursor (D12).
        best = s.best_lap_id()
        td = s._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        best_total = s.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(e.onset_dist / total_lap * best_total, e.peak_decel) for e in events]

    def lap_coasting_plot_spans(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x0, plot-x1) per coasting span on one lap, on the speed chart's SHARED axis
        for `mode`. Same projection as lap_brake_plot_positions. [] when no spans / no best
        lap (distance mode)."""
        s = self._s
        spans = self.lap_coasting_spans(lap_id)
        if not spans:
            return []
        if mode == "time":
            # Elapsed at each span edge: interp the span's odometer edges into the lap's elapsed.
            td = s._lap_time_dist_elapsed(lap_id)
            if td is None:
                return []
            _times, dists, elapsed = td
            return [(float(np.interp(sp.start_dist, dists, elapsed)),
                     float(np.interp(sp.end_dist, dists, elapsed))) for sp in spans]
        best = s.best_lap_id()
        td = s._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        # ACTIVE baseline total (reference when loaded) — the shared axis delta() uses (D12).
        best_total = s.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(sp.start_dist / total_lap * best_total, sp.end_dist / total_lap * best_total)
                for sp in spans]
