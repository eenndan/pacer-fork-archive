"""Auto coaching summary: the post-load "opportunities" model.

PACER-FREE BY CONTRACT (numpy only, no Qt). Does NOT recompute corner / driving / consistency
math; it COMPOSES the values Session already caches into a ranked, explainable shortlist of
"where to find time vs your own best lap" — every number measured and deterministic (no ML).

What it does: per corner, the median time lost vs your own best over the consistency laps
(valid, dropout-free); corners ranked by that loss, biggest first. For the top-N corners a
dominant reason (apex / braking / coasting / line) is picked from four signals, each mapped to a
comparable strength; the strongest wins (ties → a fixed reason priority). summarize returns
enough=False under MIN_LAPS consistency laps. Pure + deterministic (corners in cid order,
candidate laps ascending).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Min clean laps before coaching; the per-corner loss is a MEDIAN, ill-defined/unstable below 3.
MIN_LAPS = 3

# How many ranked corners get a dominant-reason attached (the panel's row count).
TOP_N = 3

# m prepended to a corner window when matching brake events — braking starts on the straight
# before turn-in (~1 medium-kart brake zone), upstream of the model's cornering-start enter point.
BRAKE_APPROACH_M = 30.0

# Reason ids, ordered by the tie-break PRIORITY when two signals tie: a directly-actionable input
# (apex) over a process cue (braking, coasting); raw inconsistency (line) last.
REASON_APEX = "apex"
REASON_BRAKING = "braking"
REASON_COASTING = "coasting"
REASON_LINE = "line"
REASON_NONE = "none"  # a ranked corner with no positive signal (still shows the time lost)
_REASON_PRIORITY = (REASON_APEX, REASON_BRAKING, REASON_COASTING, REASON_LINE, REASON_NONE)


@dataclass(frozen=True)
class Reason:
    """The dominant measured reason a corner is losing time, with the supporting numbers (only the
    kind-relevant fields are non-zero; all carried so UI/tests read them uniformly)."""

    kind: str
    contribution: float          # estimated s of loss attributed to this reason (the score)
    apex_speed_deficit: float    # best apex − median apex (km/h, > 0 means slower than best)
    brake_extra_s: float         # median lap's extra time-on-brakes in the window vs best (s)
    coast_extra_s: float         # extra coasting duration inside the corner vs best (s)
    sigma: float                 # cross-lap σ of time-in-corner (s)


@dataclass(frozen=True)
class Opportunity:
    """One corner's coaching row: how much time is realistically available and why."""

    cid: int                 # 1-based corner id (track order)
    direction: int           # +1 left / -1 right (for the UI glyph)
    time_lost: float         # median time lost vs the best lap's same corner (s, > 0)
    entry_dist: float        # the corner's enter odometer on the BEST lap (m) — the jump-to seek
    reason: Reason           # the dominant measured reason + numbers (only on the top-N rows)


@dataclass(frozen=True)
class Opportunities:
    """The whole summary the panel renders. `enough` is False (and `rows` empty) when there are
    fewer than MIN_LAPS consistency laps — the friendly "need more laps" state."""

    enough: bool
    n_laps: int                              # consistency laps the summary ran over
    median_lap_id: int | None                # the representative lap the reasons read off
    rows: list[Opportunity] = field(default_factory=list)


# ----------------------------------------------------------------- median-lap selection
def median_lap_id(lap_ids: list[int], lap_times: list[float]) -> int | None:
    """The candidate lap whose TIME is the median of the set — the representative lap. Even
    counts take the lower-time of the two central laps (np.argsort is stable, so a tie in time
    then resolves to the lower lap id): fully deterministic, no averaging of two laps. None for
    an empty set."""
    if not lap_ids:
        return None
    order = np.argsort(np.asarray(lap_times, float), kind="stable")
    mid = (len(order) - 1) // 2  # lower-middle index -> the median (lower of two for even n)
    return int(lap_ids[int(order[mid])])


# ---------------------------------------------------------------------- reason signals
# Each reason's raw evidence is on its own scale, so each maps to a unitless strength in [0,1) via
# evidence/(evidence + half). The strengths are comparable; the contribution is time_lost ×
# strength, so no reason overclaims the corner's own loss.

# Evidence level at which each reason hits half strength (_saturate); only sets ties between
# co-present causes — ranking insensitive within a wide band. Tuned on the D24 recordings.
_APEX_HALF_KMH = 3.0   # km/h apex deficit; below ~1 is line/GPS noise
_BRAKE_HALF_S = 0.30   # s longer/earlier than best; sub-0.1 is threshold ripple
_COAST_HALF_S = 0.30   # ~ the shortest coast the channel reports
_SIGMA_HALF_S = 0.15   # s lap-to-lap σ; below ~0.05 the line is repeatable


def _saturate(evidence: float, half: float) -> float:
    """A unitless strength in [0, 1): evidence/(evidence + half), 0 for non-positive evidence.
    Half-strength at `evidence == half`, →1 for evidence ≫ half. Makes the four reasons'
    different-unit evidence directly comparable without a magic unit conversion."""
    e = max(float(evidence), 0.0)
    return e / (e + half) if e > 0 else 0.0


def _window_brake_time(events, d_enter: float, d_exit: float) -> float:
    """Total time on the brakes (s) for the brake events whose onset falls in a corner's
    approach+window [d_enter − BRAKE_APPROACH_M, d_exit]. `events` is a list with .onset_dist /
    .duration (driving.BrakeEvent)."""
    lo = d_enter - BRAKE_APPROACH_M
    return sum(float(e.duration) for e in events if lo <= e.onset_dist <= d_exit)


def _brake_extra(med_events, best_events, med_win: tuple[float, float],
                 best_win: tuple[float, float]) -> float:
    """Extra s on the brakes vs best in the corner approach, floored at 0. An earlier onset shows
    up as more time on the brakes, so this one difference captures both 'earlier' and 'longer'.
    med_win/best_win are the corner window projected onto each lap's own odometer (see _win)."""
    return max(_window_brake_time(med_events, *med_win)
               - _window_brake_time(best_events, *best_win), 0.0)


def _coast_in_window(spans, d_enter: float, d_exit: float) -> float:
    """Total coasting DURATION (s) of the spans (driving.CoastSpan) whose span overlaps
    [d_enter, d_exit] (counted in full)."""
    total = 0.0
    for s in spans:
        if s.end_dist >= d_enter and s.start_dist <= d_exit:
            total += float(s.duration)
    return total


def _coast_extra(med_spans, best_spans, med_win: tuple[float, float],
                 best_win: tuple[float, float]) -> float:
    """Extra coasting seconds inside the corner vs best, floored at 0. med_win/best_win projected
    onto each lap's own odometer (see _win)."""
    return max(_coast_in_window(med_spans, *med_win)
               - _coast_in_window(best_spans, *best_win), 0.0)


def _pick_reason(time_lost: float, apex_speed_delta: float, sigma: float,
                 med_events, best_events, med_spans, best_spans,
                 med_win: tuple[float, float], best_win: tuple[float, float]) -> Reason:
    """Choose the dominant reason for one corner: the strongest of the four comparable strengths
    (largest wins, ties → _REASON_PRIORITY order). All raw evidence is carried on the Reason; the
    contribution is time_lost × the winning strength (≤ time_lost — never overclaims).

    LINE is the fallback (real spread but no concrete input fires); REASON_NONE when nothing fires
    (the row still shows the time lost)."""
    apex_deficit = max(-float(apex_speed_delta), 0.0)   # km/h slower than best at the apex
    brake_extra = _brake_extra(med_events, best_events, med_win, best_win)
    coast_extra = _coast_extra(med_spans, best_spans, med_win, best_win)
    sig = max(float(sigma), 0.0)

    # Comparable strengths in [0,1). A reason can only win when the corner is actually losing
    # time (time_lost > 0) — these explain a measured loss, they don't manufacture one.
    lossy = time_lost > 1e-9
    strengths = {
        REASON_APEX: _saturate(apex_deficit, _APEX_HALF_KMH) if lossy else 0.0,
        REASON_BRAKING: _saturate(brake_extra, _BRAKE_HALF_S) if lossy else 0.0,
        REASON_COASTING: _saturate(coast_extra, _COAST_HALF_S) if lossy else 0.0,
        REASON_LINE: _saturate(sig, _SIGMA_HALF_S) if lossy else 0.0,
    }
    # Largest strength; ties broken by the fixed reason priority (apex first). The contribution
    # reported is time_lost × strength (so it is bounded by the corner's own loss).
    best_kind = REASON_NONE
    best_strength = 0.0
    for kind in _REASON_PRIORITY:
        st = strengths.get(kind, 0.0)
        if st > best_strength + 1e-12:  # strictly greater (priority already favours earlier ties)
            best_strength = st
            best_kind = kind
    return Reason(
        kind=best_kind,
        contribution=time_lost * best_strength,
        apex_speed_deficit=apex_deficit,
        brake_extra_s=brake_extra,
        coast_extra_s=coast_extra,
        sigma=sig,
    )


# --------------------------------------------------------------------------- the summary
def summarize(
    corners,
    candidate_lap_ids: list[int],
    lap_times: list[float],
    corner_times_by_lap: list[list[float]],
    best_corner_times: list[float],
    sigmas_by_cid: dict[int, float],
    median_brake_events,
    best_brake_events,
    median_coast_spans,
    best_coast_spans,
    median_apex_deltas: list[float],
    *,
    corner_dist_total: float | None = None,
    median_lap_total: float | None = None,
    best_lap_total: float | None = None,
    top_n: int = TOP_N,
    min_laps: int = MIN_LAPS,
) -> Opportunities:
    """Assemble the ranked opportunities from pre-extracted, pacer-free inputs (Session owns the
    extraction; numpy-only, unit-testable on synthetic inputs).

    All arrays are aligned to candidate_lap_ids / corners and pre-restricted to the consistency
    laps + best lap. median_apex_deltas MUST use the SAME local-best baseline as the losses.
    corner_dist_total / median_lap_total / best_lap_total project each corner window onto each
    lap's own odometer before matching its brake/coast events; any None → identity projection.
    Returns Opportunities; enough=False (empty rows) when < min_laps candidate laps."""
    n_laps = len(candidate_lap_ids)
    med_id = median_lap_id(candidate_lap_ids, lap_times)
    if n_laps < min_laps or not corners:
        return Opportunities(enough=False, n_laps=n_laps, median_lap_id=med_id, rows=[])

    times = np.asarray(corner_times_by_lap, float)  # (n_laps, n_corners)
    best = np.asarray(best_corner_times, float)     # (n_corners,)
    n_corners = len(corners)
    # Per-corner median time lost vs the best lap's same corner, over the candidate laps. Guard
    # a ragged matrix (a degenerate lap projecting to fewer corners is already filtered upstream,
    # but stay defensive) by only using columns present for every lap.
    if times.ndim != 2 or times.shape[1] != n_corners or len(best) != n_corners:
        return Opportunities(enough=False, n_laps=n_laps, median_lap_id=med_id, rows=[])
    losses = np.median(times - best[None, :], axis=0)  # (n_corners,)

    # Project [enter,exit] onto one lap's own odometer (scale lap_total/corner_dist_total); identity
    # if a total is missing. A lap's brake/coast events live in its own odometer, so this matches frames.
    def _win(c, lap_total: float | None) -> tuple[float, float]:
        if (corner_dist_total and lap_total and corner_dist_total > 0
                and lap_total != corner_dist_total):
            scale = lap_total / corner_dist_total
            return float(c.enter) * scale, float(c.exit) * scale
        return float(c.enter), float(c.exit)

    # Build a row per corner with a positive median loss; rank by the loss (biggest first).
    ranked_idx = [i for i in np.argsort(-losses, kind="stable") if losses[i] > 1e-9]

    rows: list[Opportunity] = []
    for rank, i in enumerate(ranked_idx):
        c = corners[i]
        attach_reason = rank < top_n
        if attach_reason:
            reason = _pick_reason(
                time_lost=float(losses[i]),
                apex_speed_delta=(float(median_apex_deltas[i])
                                  if i < len(median_apex_deltas) else 0.0),
                sigma=float(sigmas_by_cid.get(c.cid, 0.0)),
                med_events=median_brake_events, best_events=best_brake_events,
                med_spans=median_coast_spans, best_spans=best_coast_spans,
                med_win=_win(c, median_lap_total), best_win=_win(c, best_lap_total),
            )
        else:
            reason = Reason(kind=REASON_NONE, contribution=0.0, apex_speed_deficit=0.0,
                            brake_extra_s=0.0, coast_extra_s=0.0,
                            sigma=float(sigmas_by_cid.get(c.cid, 0.0)))
        rows.append(Opportunity(
            cid=c.cid, direction=c.direction, time_lost=float(losses[i]),
            entry_dist=float(c.enter), reason=reason,
        ))
    return Opportunities(enough=True, n_laps=n_laps, median_lap_id=med_id, rows=rows)


# ------------------------------------------------------------------ UI sentence helper
def reason_sentence(opp: Opportunity) -> str:
    """The human, numbers-only coaching sentence for one opportunity's dominant reason. Kept
    here (next to the model) so the panel and any export read ONE phrasing and can't drift."""
    r = opp.reason
    if r.kind == REASON_APEX:
        return f"carry more apex speed (−{r.apex_speed_deficit:.1f} km/h)"
    if r.kind == REASON_BRAKING:
        return f"brake later / shorter (+{r.brake_extra_s:.2f} s on the brakes)"
    if r.kind == REASON_COASTING:
        return f"back to throttle sooner (+{r.coast_extra_s:.2f} s coasting)"
    if r.kind == REASON_LINE:
        return f"be consistent here (σ {r.sigma:.2f} s)"
    return "find time here"
