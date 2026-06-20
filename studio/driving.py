"""Driving channels (F5): brake events, coasting spans, per-corner grip utilization.

PACER-FREE (numpy only). Reads the already-validated kart-frame g series (gmeter.py) and
the lap's odometer/elapsed/speed, and labels three things on it:

  * BRAKE EVENTS — contiguous decel below -theta_b (gmeter sign: long_g>0 accel, <0 brake),
    held open with Schmitt hysteresis (release above -theta_b*RELEASE_RATIO) so pressure
    ripple at the threshold doesn't shatter one zone into many.
  * COASTING SPANS — |long_g|<theta_c AND |lat_g|<theta_lat (neither braking/accelerating nor
    loaded in a corner) AND only a mild speed change (so a steady throttle pull isn't a coast).
  * PER-CORNER GRIP UTILIZATION — median(|g|)/envelope_max inside each corner window, where
    |g|=hypot(lat,long) and envelope_max is the lap's own peak |g|. Median is robust to a
    one-sample turn-in IMU spike.

Thresholds self-scale: derived from THIS recording's own moving-sample g distribution, all
floored so a no-braking session can't manufacture false events. The g series is already
low-passed in gmeter (0.15 s); a short SMOOTH_S boxcar before thresholding stops a lone IMU
sample toggling a brake event.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import boxcar

# --- model constants -------------------------------------------------------------------
SMOOTH_S = 0.10  # boxcar on long_g before thresholding so a lone IMU sample can't toggle an event
BRAKE_MEDIAN_PCT = 50.0   # theta_b = median of braking-only decel (duty-cycle independent)
BRAKE_SAMPLE_FLOOR = 0.05  # decel above ~0.05g noise floor counts as braking for the theta_b set
THETA_B_FLOOR = 0.10      # hard floor so a no-braking session can't manufacture brake events
RELEASE_RATIO = 0.6       # Schmitt release at theta_b*this so threshold ripple doesn't fragment a zone
MIN_BRAKE_S = 0.20        # drop brake runs below the shortest real brake application
COAST_PERCENTILE = 40.0   # low percentile of |long_g|/|lat_g| setting the coast bands
THETA_C_FLOOR = 0.04      # coast bands can't be tighter than the IMU floor
THETA_LAT_FLOOR = 0.05
COAST_MAX_SPEED_FRAC = 0.06  # max |dv|/v across a span so a steady throttle pull isn't a "coast"
MIN_COAST_S = 0.30        # drop coast blips between brake-release and throttle-pickup
MOVING_KMH = 14.4         # 4.0 m/s, matching gmeter._MOVING_MS; below this a sample is "stopped"


@dataclass(frozen=True)
class Thresholds:
    """Brake/coast thresholds derived from one session's own g distribution (g, positive
    magnitudes)."""

    theta_b: float       # brake decel threshold: a brake event is long_g < -theta_b
    theta_c: float       # coast longitudinal band: |long_g| < theta_c
    theta_lat: float     # coast lateral band: |lat_g| < theta_lat
    n_moving: int        # moving samples the distribution was measured over
    # The measured distribution percentiles that motivate the values (for the load-time print).
    brake_p75: float
    brake_p90: float
    brake_max: float

    def describe(self) -> str:
        return (f"driving channels: theta_b={self.theta_b:.3f} g (brake), "
                f"theta_c={self.theta_c:.3f} g / theta_lat={self.theta_lat:.3f} g (coast); "
                f"over {self.n_moving} moving samples the braking decel ran "
                f"p75={self.brake_p75:.3f}, p90={self.brake_p90:.3f}, "
                f"max={self.brake_max:.3f} g.")


@dataclass(frozen=True)
class BrakeEvent:
    """One detected braking zone within a lap, in that lap's own odometer/elapsed space."""

    onset_dist: float    # lap odometer (m) where the brake application begins
    onset_time: float    # elapsed (s, from the lap start) at the onset
    peak_decel: float    # peak braking decel over the event (g, positive magnitude)
    duration: float      # how long the event lasts (s)


@dataclass(frozen=True)
class CoastSpan:
    """One coasting span within a lap (neither braking nor accelerating nor cornering),
    in that lap's own odometer space."""

    start_dist: float    # lap odometer (m) where the coast begins
    end_dist: float      # lap odometer (m) where it ends
    duration: float      # how long it lasts (s)


def _smooth_window(t: np.ndarray) -> int:
    """Samples spanning SMOOTH_S given the (roughly uniform) g-series time step."""
    if len(t) < 3:
        return 1
    dt = float(np.median(np.diff(t)))
    return max(int(round(SMOOTH_S / max(dt, 1e-9))), 1)


def derive_thresholds(long_g, lat_g, speed_kmh) -> Thresholds:
    """Derive (theta_b, theta_c, theta_lat) over the session's MOVING samples. `long_g`/`lat_g`
    are the kart-frame g series (gmeter sign: long_g>0 accel, <0 brake); `speed_kmh` aligned.
    Every threshold is floored, so a no-braking session yields no false events."""
    long_g = np.asarray(long_g, float)
    lat_g = np.asarray(lat_g, float)
    speed_kmh = np.asarray(speed_kmh, float)
    n = min(len(long_g), len(lat_g), len(speed_kmh))
    long_g, lat_g, speed_kmh = long_g[:n], lat_g[:n], speed_kmh[:n]
    moving = speed_kmh > MOVING_KMH
    if not np.any(moving):
        moving = np.ones(n, dtype=bool)  # degenerate: use all samples rather than divide by 0
    lg = long_g[moving]
    la = lat_g[moving]
    decel = np.maximum(-lg, 0.0)  # braking decel magnitude (0 when accelerating/coasting)
    # theta_b = median of the braking-only decel (samples past BRAKE_SAMPLE_FLOOR, so light
    # throttle modulation near zero doesn't drag the median down).
    braking = decel[decel > BRAKE_SAMPLE_FLOOR]
    if braking.size:
        theta_b = max(float(np.percentile(braking, BRAKE_MEDIAN_PCT)), THETA_B_FLOOR)
    else:
        theta_b = THETA_B_FLOOR  # ~no braking in the session: floor it (no false events)
    theta_c = max(float(np.percentile(np.abs(lg), COAST_PERCENTILE)), THETA_C_FLOOR)
    theta_lat = max(float(np.percentile(np.abs(la), COAST_PERCENTILE)), THETA_LAT_FLOOR)
    return Thresholds(
        theta_b=theta_b, theta_c=theta_c, theta_lat=theta_lat, n_moving=int(np.sum(moving)),
        brake_p75=float(np.percentile(braking, 75.0)) if braking.size else 0.0,
        brake_p90=float(np.percentile(braking, 90.0)) if braking.size else 0.0,
        brake_max=float(decel.max()) if decel.size else 0.0,
    )


def brake_events(dist, elapsed, long_g, theta_b: float) -> list[BrakeEvent]:
    """Detect braking zones on one lap (aligned dist/elapsed/long_g, same lap; gmeter sign).
    Held open with Schmitt hysteresis (see RELEASE_RATIO). Returns events in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    g = np.asarray(long_g, float)
    n = min(len(dist), len(elapsed), len(g))
    dist, elapsed, g = dist[:n], elapsed[:n], g[:n]
    if n < 2:
        return []
    g = boxcar(g, _smooth_window(elapsed))
    hi = float(theta_b)               # ENTER braking below -hi
    lo = float(theta_b) * RELEASE_RATIO  # RELEASE only once decel recovers above -lo
    out: list[BrakeEvent] = []
    i = 0
    while i < n:
        if g[i] < -hi:
            j0 = i
            while i > 0 and g[i - 1] < -lo:  # extend backwards into the lo band (onset)
                j0 -= 1
                i -= 1
            j1 = j0
            while j1 + 1 < n and g[j1 + 1] < -lo:  # extend forwards until decel releases
                j1 += 1
            seg = g[j0:j1 + 1]
            duration = float(elapsed[j1] - elapsed[j0])
            if duration >= MIN_BRAKE_S:
                out.append(BrakeEvent(
                    onset_dist=float(dist[j0]),
                    onset_time=float(elapsed[j0]),
                    peak_decel=float(-seg.min()),  # deepest decel as a positive magnitude
                    duration=duration,
                ))
            i = j1 + 1
        else:
            i += 1
    return out


def coasting_spans(dist, elapsed, speed_kmh, long_g, lat_g,
                   theta_c: float, theta_lat: float) -> list[CoastSpan]:
    """Detect coasting spans on one lap (|long_g|<theta_c AND |lat_g|<theta_lat AND speed
    barely changing). Aligned arrays, same lap; spans in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    speed_kmh = np.asarray(speed_kmh, float)
    lg = np.asarray(long_g, float)
    la = np.asarray(lat_g, float)
    n = min(len(dist), len(elapsed), len(speed_kmh), len(lg), len(la))
    if n < 2:
        return []
    dist, elapsed, speed_kmh = dist[:n], elapsed[:n], speed_kmh[:n]
    lg, la = lg[:n], la[:n]
    w = _smooth_window(elapsed)
    lg = boxcar(lg, w)
    la = boxcar(la, w)
    coast = (np.abs(lg) < theta_c) & (np.abs(la) < theta_lat) & (speed_kmh > MOVING_KMH)
    out: list[CoastSpan] = []
    i = 0
    while i < n:
        if coast[i]:
            j0 = i
            while i + 1 < n and coast[i + 1]:
                i += 1
            j1 = i
            duration = float(elapsed[j1] - elapsed[j0])
            v0 = float(speed_kmh[j0])
            dv_frac = abs(float(speed_kmh[j1]) - v0) / max(v0, 1e-6)
            if duration >= MIN_COAST_S and dv_frac < COAST_MAX_SPEED_FRAC:
                out.append(CoastSpan(start_dist=float(dist[j0]), end_dist=float(dist[j1]),
                                     duration=duration))
        i += 1
    return out


def corner_grip(dist, long_g, lat_g, windows) -> list[float]:
    """Per-corner grip utilization: median(hypot(lat,long)) inside each (enter,exit) odo window
    / lap-envelope max, in (0,1]. One float per window (0.0 if empty)."""
    dist = np.asarray(dist, float)
    lg = np.asarray(long_g, float)
    la = np.asarray(lat_g, float)
    n = min(len(dist), len(lg), len(la))
    dist, lg, la = dist[:n], lg[:n], la[:n]
    gmag = np.hypot(la, lg)
    envelope_max = float(gmag.max()) if n else 0.0
    if envelope_max <= 0:
        return [0.0 for _ in windows]
    out: list[float] = []
    for d0, d1 in windows:
        idx = np.flatnonzero((dist >= d0) & (dist <= d1))
        if len(idx):
            out.append(float(np.median(gmag[idx])) / envelope_max)
        else:
            out.append(0.0)
    return out
