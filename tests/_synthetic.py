"""ONE synthetic bare-Session factory for the pure-Python studio tests.

Not a test (no CTest registration) — a helper module the test_*.py files import. The studio
tests drive REAL Session math (delta/odometer/scrub conversions, nearest-in-lap, lap_at_time)
on a Session built via __new__ with its private caches seeded directly — the seeding idiom
Session explicitly supports for tests. Each file used to hand-roll its own variant, and some
seeded the legacy 2-tuple (times, dists) `_dist_cache` form, which kept a test-only upgrade
shim alive in production. This module is now the single place that knows the cache shapes:
  * `_dist_cache[lap_id] = (times, dists, elapsed)` — the CURRENT 3-tuple form
    (elapsed = times - times[0], exactly what `Session._lap_time_dist_elapsed` memoizes);
  * `_valid_cache` / `_best_cache` — the `valid_lap_ids()` / `best_lap_id()` memo slots, so
    the REAL methods serve the seeded ids without a pacer `laps` object.
Anything pacer-backed beyond that (lap_window, lap_at_time, g_at_time, ...) stays a per-test
stub at the call site — only what a test genuinely needs is faked.
"""
import numpy as np

from studio.session import Session


def odometer(n, dt, t0, total_dist, profile=None):
    """A monotonic (times, dists) lap: `times` start at t0 with step dt; `dists` integrates a
    positive speed `profile` (sampled over u ∈ [0, π]) normalized to end at total_dist. The
    default slow-fast-slow sin² profile keeps every step positive (strictly increasing
    odometer) and NON-uniform, so distance<->time is a real, non-linear interpolation — a
    constant-speed lap would make distance mode trivially equal to a scaled time mode."""
    if profile is None:
        def profile(u):
            return 1.0 + np.sin(u) ** 2
    times = t0 + np.arange(n) * dt
    speed = profile(np.linspace(0.0, np.pi, n))
    cum = np.cumsum(speed)
    dists = (cum - cum[0]) / (cum[-1] - cum[0]) * total_dist
    return times, dists


def seed_lap(session, lap_id, times, dists):
    """Seed ONE lap's arrays into `session._dist_cache`, always in the current 3-tuple
    (times, dists, elapsed) form so production never sees a legacy 2-tuple entry."""
    times = np.asarray(times, dtype=float)
    dists = np.asarray(dists, dtype=float)
    session._dist_cache[lap_id] = (times, dists, times - times[0])


def seed_cols(session, lap_id, times, dists):
    """Seed one `_cols_cache` 5-tuple (times, xs, ys, full_speed m/s, cum_distances) for a
    STRAIGHT-LINE lap along the x-axis (xs = odometer, ys = 0), so the sector-midpoint→trace
    projection geometry is consistent with the odometer the splits interpolate on. Feeds the
    paths that read the bulk `lap_columns` crossing directly (`_lap_arrays` / sector splits)."""
    times = np.asarray(times, float)
    dists = np.asarray(dists, float)
    if not hasattr(session, "_cols_cache"):  # bare Session.__new__ — the slot needs creating
        session._cols_cache = {}
    session._cols_cache[lap_id] = (
        times, dists.copy(), np.zeros_like(dists), np.gradient(dists, times), dists.copy(),
    )


def bare_session(laps=None, *, best=None, valid=None):
    """A bare Session (Session.__new__ — no pacer, no telemetry file, no Qt event loop).

    laps:  optional {lap_id: (times, dists)} seeded into `_dist_cache` (see `seed_lap`) —
           feeds the real delta_between / delta_at_lap / media_time_at_plot_x / plot_x_at_-
           media_time / nearest_*_in_lap math.
    best:  optional best-lap id — seeds the `best_lap_id()` memo (`_best_cache`), which also
           drives `best_lap_total_distance()` off the seeded cache.
    valid: optional iterable of valid lap ids — seeds the `valid_lap_ids()` memo
           (`_valid_cache`)."""
    s = Session.__new__(Session)
    s._dist_cache = {}
    for lap_id, (times, dists) in (laps or {}).items():
        seed_lap(s, lap_id, times, dists)
    if best is not None:
        s._best_cache = best
    if valid is not None:
        s._valid_cache = list(valid)
    return s
