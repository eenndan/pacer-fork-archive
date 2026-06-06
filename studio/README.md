# pacer studio

A local **PySide6 + pyqtgraph** desktop app for race-telemetry analysis — a greenfield UI
on top of the existing C++ `pacer` core (reused via its nanobind Python bindings). Chosen
for a single-language, LLM-editable codebase that still nails draggable map handles and
frame-accurate video↔telemetry sync (all in Python — see [the spike](spike_video_sync.py)).

## Run

```bash
pixi run studio                              # short demo clip (hero6 sample, map+video only)
pixi run studio -- /path/to/GX010060.MP4     # your GoPro session (multi-lap)
pixi run studio -- a.MP4 b.MP4               # chaptered recording (chained in order)
pixi run studio -- --interp session.MP4      # opt in to C++ timestamp interpolation
```

First `pixi run studio` resolves the env once (installs `pyside6`/`pyqtgraph` from the
manifest). Equivalent without pixi: `python -m studio [files]`.

Diagnose a file headlessly (sample stats, GPS noise, time axis, lap segmentation):

```bash
pixi run python -m studio.diagnose -- /path/to/file.MP4 [--interp] [--clean]
```

Render the map offscreen to PNG + print jitter/signal metrics (the GPS-denoise feedback loop;
`--window N` overrides `SMOOTH_WINDOW`, `--window 1` = raw baseline, `--notebook-ref` adds the
notebook's w=9 reference overlay):

```bash
pixi run python -m studio.denoise_check -- /path/to/file.MP4 [--window N] [--tag T] [--notebook-ref] [--gaps]
```

`--gaps` additionally prints per-lap GPS-gap reconstruction metrics (chord metres, borrow vs
reference vs spline fill) and renders the gap-filled map (measured solid + inferred dashed). Pure-Python
gap-fill unit tests live in [`tests/test_gapfill.py`](../tests/test_gapfill.py) (`python tests/test_gapfill.py`).

## Layout

```
┌──────────────┬───────────────────────────┐
│  VideoView   │   MapView (track + lines) │   video ⇄ telemetry sync:
├──────────────┼───────────────────────────┤   • video plays → red map marker sweeps
│  LapTable    │   PlotsView (speed/delta) │   • drag the marker → video seeks
└──────────────┴───────────────────────────┘   • drag a plot cursor → scrub within the lap
                                                • drag start/sector lines → re-segment laps
```

## Modules (one responsibility each)

| File | Role |
|------|------|
| [session.py](session.py) | Loads GPMF → `pacer.Laps`; exposes trace/lap/delta arrays + timing-line write-back. Owns the load/segmentation pipeline (primary `pacer` user). |
| [tracks.py](tracks.py) | Registry of known tracks (Daytona MK); detects the track by centroid and gives its fixed start/finish line. The only other module that names `pacer` (geometry only). |
| [video_view.py](video_view.py) | `QMediaPlayer` + `QVideoWidget`; emits `positionChanged(s)`, exposes `seek(s)` + `is_playing()`/`pause()`/`play()`. |
| [map_view.py](map_view.py) | Best lap (faint) + current/playing lap (highlighted) + **freely-draggable** start/sector timing lines + video marker. Each lap is drawn as measured (solid) + reconstructed gap-fill (dashed/dimmed) segments. The full all-laps trace is intentionally not drawn (perf + clarity). |
| [gapfill.py](gapfill.py) | **GPS-gap reconstruction (map only)** — pure numpy. Detects interior dropouts and fills them with cross-lap borrow (primary) / reference centerline (fallback) / spline, tagged measured-vs-inferred. No `pacer`. |
| [reference.py](reference.py) + [mk_centerline.json](mk_centerline.json) | Georeferenced Daytona MK centerline (traced from `gmaps_pict.png`, similarity-ICP aligned to the GPS aggregate) — the gap-fill fallback for sections no lap covers. Rebuild via [build_reference.py](build_reference.py). |
| [plots_view.py](plots_view.py) | Speed (top) + lap-vs-best delta (bottom) on **one shared, x-linked x-axis** (dist/time toggle drives both; delta aligned by **normalized distance** → endpoint = laptime diff), so the two cursors always align. Downsampled/clipped curves + a synced cursor that is also a **draggable scrubber** + a **hover dot** on the delta curve — pacer-free, it only emits `scrubStarted`/`scrubMoved(x, mode)`/`scrubEnded` (mode = `time`\|`distance`); app converts + seeks and owns the live Δ/speed readout box. |
| [lap_table.py](lap_table.py) | Lap time / dist / entry speed + per-sector split columns (S1…Sn) once sectors are added. Multi-select to compare; **▶** marks the playing lap, blue = selection, green = best. |
| [app.py](app.py) | Assembles panels in splitters and wires the cross-panel signals. |

## Gotchas / notes

- **Coordinate space:** the trace *and* the timing lines live in **local meters** (`cs.local`).
  `pick_random_start()` / `update()` must run **after** `set_coordinate_system()`. (See
  `pacer/laps/laps.cpp` — `Update` converts the lines back to GPS internally for crossing tests.)
- **Sectors write-back:** assign wholesale — `laps.sectors = pacer.Sectors(start_line=…, sector_lines=[…])` — then `laps.update()`. (Verified to propagate across the binding.)
- **Data cleaning (`session._clean`):** real GoPro sessions have a stationary lead-in where
  GPS isn't locked and spikes wildly (jumps up to ~80 km). We trim the non-moving lead-in/cool-down
  and drop lone teleport glitches. Without this the trace, map zoom, and lap segmentation all break.
- **GPS quality gating (`session._gate_quality`):** the C++ core surfaces the GoPro **GPS9 DOP +
  fix-type** on `GPSSample` (new `.dop`/`.fix`; parsed in `pacer/gps-source/gps-source.cpp`). At
  load we drop fixes with no 3D lock (`fix<3`) or poor geometry (`dop>10`) — conservative. The
  older GPS5 stream carries neither (sentinels `fix=-1`, `dop=-1.0`) → "unknown" is always KEPT.
- **GPS track smoothing (`session._smooth_track`, `SMOOTH_WINDOW=13`):** the original map plotted
  RAW GPS coords, so ~3 m position jitter made the trace look noisy. We now apply the notebook's
  proven boxcar moving average (`notebooks/interpolation.ipynb`) — edge-corrected and split at
  time gaps — to lat/lon/alt ONCE at load, *before* the points reach the core. Because the source
  coordinates are smoothed, the trace and every derived quantity (distances, segmentation, delta,
  sector splits) stay consistent. w=13 (~1.3 s @ 10 Hz) cuts the high-frequency jitter ~39% / the
  heading jitter ~91% while preserving genuine lap-to-lap racing-line differences and NOT clipping
  corner apexes (w≥21 starts cutting corners). Tune/measure with `studio/denoise_check.py`.
- **Timing is naive by default.** The C++ `interpolate_timestamps` diverges on long/noisy sessions
  (compresses lap times, overruns the video). `--interp` opts in, but the result is validated
  (monotonic + within the video duration) and silently falls back to naive if it's bad.
- **Lap validity is adaptive** (`session.valid_lap_ids`): laps within a band around the median lap
  time, so short double-crossings of the start line don't pollute the "best" lap.
- **Delta-to-best** (`session.delta`) is aligned by **normalized distance fraction** (s∈[0,1]) so a
  lap's delta *ends exactly at its laptime difference*; raw cum-distance alignment did not. The
  speed + delta plots share **one x-axis** driven by the dist/time toggle and kept x-linked
  (`delta(ids, x_mode=…)`: distance = s×best_distance metres, time = time-into-lap), so the two
  cursors always line up vertically at the same moment. `session.delta_at_time(t)` gives the
  current-moment Δ-to-best for the live readout box.
- **Per-sector splits** (`session.lap_sector_splits`) project each sector line to a cum-distance on
  each lap and split the time there — correct (sums to lap time) for every lap, no reliance on
  fragile geometric crossing of short lines.
- **GPS gap reconstruction (`session.lap_trace_segments` → `gapfill.py`, MAP ONLY):** where a lap's
  GPS has an interior dropout (a run removed by the quality gate, or a real outage), the trace used
  to draw a straight **chord** across the hole. Now each lap is drawn as measured runs + inferred
  fills. A gap = an interior point-to-point time jump > ~0.35 s (≥3 missing samples @ 10 Hz); the
  lap's open start/finish ends are not gaps. Filled by, in order: (1) **cross-lap borrow** (the track
  is identical every lap → take a donor lap's sub-polyline between the points nearest the gap mouths
  and pin it with a similarity transform → the real corner shape, connected continuously), (2) a
  georeferenced **reference centerline** only where no lap covers the section (`reference.py`), (3) a
  **spline** for very short gaps. Inferred segments draw **dashed + dimmed** so real GPS is always
  distinguishable from reconstruction. Segments are cached per lap (built once, never per frame). On
  `GX010060.MP4`: 7 gaps / 222 m of chord → 6 borrow + 1 spline, 0 reference, 0 unfilled.
  **MAP-ONLY guarantee:** `gapfill`/`reference` are pure numpy and read the unchanged kept-point
  arrays — `session.delta`, `lap_sector_splits`, `cum_distances`, `valid_lap_ids` are byte-identical
  (proven same JSON MD5 vs the base branch). Inspect via `denoise_check --gaps`.
- **Timing lines are placed freely** (no snap-to-trace); dragging redraws live and re-segments the
  laps once on release.
- **Draggable plot-cursor scrubber** (a fine, lap-scoped scrub; the full-video slider stays as-is):
  the speed + delta cursors are `movable` `InfiniteLine`s. Dragging either seeks the video **within
  the lap the playhead is currently in**, clamped to that lap's start/end. `plots_view` stays
  pacer-free — it emits only the raw plot-x + the shared axis mode (`scrubMoved(x, mode)`); `app.py`
  (which owns session + video) converts to a media time, seeks, and re-syncs both cursors + the
  slider + the map marker. The x↔media-time mapping lives in `session` (pure numpy on cached per-lap
  `(times, dists)`): `media_time_at_plot_x` / `plot_x_at_media_time`, with `mode`:
  - **time:**  `t = lap_start + x`  (both plots; x = time-into-lap)
  - **distance (= delta):**  `s = x / best_distance` → `dist_in_lap = s·lap_total` → `np.interp` →
    time. Both plots share this ONE distance axis (x = s×best_distance, the metres axis the curves
    are drawn on), so the speed and delta cursors map a given moment to the **same x** and coincide
    vertically. The plots are permanently **x-linked** (`p_delta.setXLink(p_speed)`), so pan/zoom on
    one follows the other in both modes. (`delta` is kept as a readable alias of `distance` in the
    conversion helpers — identical math.) Source of truth is the media time ("two lines, one truth").
  **Throttle + pause/resume:** seeks are coalesced to **≤1 per 30 Hz tick** (store latest target,
  one seek/tick) so 4K HEVC stays responsive; on grab we pause if playing (remembering it), on
  release we resume iff it was playing. **No feedback loop:** while dragging, the playback tick's
  position-driven re-placement is ignored (`_user_dragging`) and the tick skips its normal apply
  (only the coalesced seek runs); programmatic `setValue` is `_suppress`-guarded so it can never
  masquerade as a user drag. Round-trip/clamp + cursor-coincide tests in `tests/test_scrub_conversion.py`.
- **Live Δ/speed readout + delta-plot hover dot:** an always-on box above the plots
  (`app._update_diff_box`) shows the **current-moment Δ-to-best (priority) + speed** from
  `session.delta_at_time` / `speed_at_time` — green when ahead of best, red when behind — updating
  live as the video plays or the cursor scrubs. The delta plot additionally shows a **hover dot**
  (`ScatterPlotItem` + `TextItem` on `scene().sigMouseMoved`) that snaps to the nearest delta-curve
  sample under the mouse and labels its Δ value (+ distance/time there), independent of the playback
  cursor, hidden on mouse-leave. The hover handler is a cheap nearest-index lookup on the cached
  curve arrays (no re-plot); `plots_view` stays pacer-free (the box's values come from `session`
  via `app`; the hover reads only the curve already drawn).
- **Performance:** UI sync runs on a ~30 Hz `QTimer` off the video present path; plot curves are
  downsampled+clipped with antialias off and autorange frozen; the map draws ≤2 laps. 4K HEVC
  decodes ~61 fps via VideoToolbox hardware decode — playback stays smooth, incl. with a lap selected.
- **Video sync** uses `QMediaPlayer.positionChanged`. For sub-frame precision, `QVideoSink.videoFrameChanged` / `QVideoFrame.startTime()` are available (the spike measured them frame-accurate, ~29 ms vs pacer's clock).
- **Sources:** GPMF/GoPro `.MP4` only — the u-blox `.dat` reader isn't bound yet. pacer supplies the telemetry time axis; the app brings its own video player (pacer doesn't decode pixels).
- `_smoke.py` is a headless self-test: `python -m studio._smoke`.

## Next ideas

See [PLAN.md](PLAN.md) for the prioritized backlog. In short: more tracks in `tracks.py` (+ real
auto-detection), persist sector/start-line config per file, pure-Python tests for `session.py`,
verify multi-file chaptered sessions, and polish (keyboard shortcuts, optional snap toggle).
