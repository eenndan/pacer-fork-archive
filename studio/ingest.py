"""Ingest: the pacer-touching GoPro/GPMF data-loading layer.

This is the data/control-layer module that owns the IO that must reach into the bound `pacer`
module: building the `SequentialGPSSource` chain over one or more GoPro chapters and reading the
raw GPS / IMU streams off it. It turns GoPro files into (cleaned-later) GPS samples + per-chapter
durations and the raw IMU arrays; it does NOT build the `pacer.Laps` model or do any analysis —
`studio/session.py` orchestrates that on top of these readers.

Architecture rule: alongside `session.py` and `tracks.py`, `ingest.py` MAY touch the pacer
bindings (it is the GoPro/GPMF data-loading layer). VIEW modules stay pacer-free. The pure-numpy
signal cleaners live in `studio/_signal.py`; this module is specifically the pacer-touching IO.
"""

from __future__ import annotations

import numpy as np

import pacer


def chain_sources(paths):
    """Build the left-leaning `SequentialGPSSource` chain over one or more GoPro chapters and
    return (head, owners, durations).

    For multiple chapters the per-file GPMF sources are folded into a chain whose C++
    `current_time_span()` / `read_accl/grav/cori` already return GLOBAL (cumulative) times —
    chapter k+1 is shifted by the sum of the earlier chapters' durations (see pacer/gps-source
    SequentialGPSSource) — so everything comes out on ONE continuous, monotonic global clock with
    no per-chapter reset. `owners` keeps every intermediate source alive while the caller iterates
    `head`. `durations` is each chapter's own 0-based media duration, in `paths` order — the
    offset table the video layer uses for global<->chapter mapping. The single shared chain build
    behind both `read_gpmf` and `read_imu` (pacer access stays in this ingest layer)."""
    owners = [pacer.GPMFSource(paths[0])]
    durations = [owners[0].get_total_duration()]
    head = owners[0]
    for p in paths[1:]:
        nxt = pacer.GPMFSource(p)
        owners.append(nxt)
        durations.append(nxt.get_total_duration())
        head = pacer.SequentialGPSSource(head, nxt)
        owners.append(head)  # keep the chain alive while we iterate
    return head, owners, durations


def read_gpmf(paths):
    """Iterate one or more GoPro files, returning (samples, spans, naive_times, durations).

    The per-file GPMF sources are folded into the shared `chain_sources` chain, so the samples
    come out on ONE continuous, monotonic global clock with no per-chapter reset, and lap
    segmentation / distances / delta all span chapter boundaries automatically.

    `durations` is each chapter's own 0-based media duration (from the GPMF/media), in the same
    order as `paths` — the offset table the video layer uses for global<->chapter mapping."""
    head, _owners, durations = chain_sources(paths)

    samples, spans, naive = [], [], []
    head.seek(0)
    while not head.is_end():
        a, b = head.current_time_span()
        chunk = []
        head.read_samples(lambda s, i, n, _c=chunk: _c.append((s, i, n)))
        for s, i, n in chunk:
            samples.append(s)
            spans.append((a, b))
            naive.append(a + (b - a) * (i / n if n else 0.0))
        head.next()
    return samples, spans, naive, durations


def read_imu(paths):
    """Read the GoPro IMU streams (ACCL accelerometer, GRAV gravity vector, CORI camera
    orientation) for the whole recording, on the global MEDIA clock — the same basis as the GPS
    trace and the video. Uses the identical left-leaning `SequentialGPSSource` chain as
    `read_gpmf` (the shared `chain_sources`), whose C++ `read_accl/grav/cori` shift each later
    chapter by the cumulative duration, so a multi-chapter recording comes out on one continuous
    clock with no per-chapter reset (lining up with the telemetry trace's global axis used for
    video sync).

    Returns (accl, grav, cori) as numpy arrays — accl/grav: (N,4) [t,x,y,z]; cori: (N,5)
    [t,w,x,y,z] — or empty (0,4)/(0,5) arrays for an older camera that lacks a stream. The
    studio gmeter module resolves the camera->kart frame transform on top of these raw axes.
    """
    head, _owners, _durations = chain_sources(paths)

    accl, grav, cori = [], [], []
    head.read_accl(lambda s: accl.append((s.time, s.x, s.y, s.z)))
    head.read_grav(lambda s: grav.append((s.time, s.x, s.y, s.z)))
    head.read_cori(lambda s: cori.append((s.time, s.w, s.x, s.y, s.z)))
    a = np.asarray(accl, float).reshape(-1, 4)
    g = np.asarray(grav, float).reshape(-1, 4)
    c = np.asarray(cori, float).reshape(-1, 5)
    return a, g, c
