"""StudioWindow: assembles the panels and wires the cross-panel sync.

Layout (resizable splitters):
    ┌──────────────┬───────────────────────────┐
    │  VideoView   │   MapView (track + lines) │
    ├──────────────┼───────────────────────────┤
    │  LapTable    │   PlotsView (speed/delta) │
    └──────────────┴───────────────────────────┘
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter

from .lap_table import LapTable
from .map_view import MapView
from .plots_view import PlotsView
from .session import DEFAULT_SAMPLE, Session, fmt_time
from .video_view import VideoView


class StudioWindow(QMainWindow):
    def __init__(self, paths: list[str], interpolate: bool = False):
        super().__init__()
        self.setWindowTitle("pacer studio")
        self.resize(1340, 840)

        print("studio: loading telemetry…", flush=True)
        self.session = Session.load(paths, interpolate=interpolate)
        print(f"studio: {self.session.laps.point_count()} points, "
              f"{self.session.lap_count()} laps.", flush=True)

        self.video = VideoView(self.session.video_path)
        self.map = MapView(self.session)
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)

        left = QSplitter(Qt.Vertical)
        left.addWidget(self.video)
        left.addWidget(self.table)
        left.setSizes([460, 360])

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.map)
        right.addWidget(self.plots)
        right.setSizes([460, 380])

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([520, 820])
        self.setCentralWidget(main)

        # --- cross-panel wiring ---
        self.video.positionChanged.connect(self._on_position)
        self.map.seek_requested.connect(self.video.seek)
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_laps_selected)

        self._select_default()

    def _select_default(self):
        """Pre-select the two fastest laps so speed + a real delta-to-best show on launch."""
        rows = sorted(self.session.lap_rows(), key=lambda r: r["time"])
        ids = [r["idx"] for r in rows[:2]]
        self.table.select(ids)
        self._on_laps_selected(ids)

    def _on_laps_selected(self, ids):
        self.plots.set_laps(ids)
        self.map.highlight_laps(ids)
        if ids:  # F1: jump the video to the earliest selected lap's start.
            self.video.seek(self.session.laps.start_timestamp(min(ids)))

    def _on_position(self, t: float):
        self.map.set_marker_time(t)
        self.plots.set_cursor_time(t)

        lap_id = self.session.lap_at_time(t)  # F3: which lap is on the video
        self.table.set_current_lap(lap_id)
        sp = self.session.speed_at_time(t)  # F2: time / speed / lap readout
        speed = f"{sp:.1f}" if sp is not None else "-"
        lap = lap_id if lap_id is not None else "-"
        self.video.set_readout(f"t = {fmt_time(t)}   speed = {speed} km/h   lap {lap}")

    def _on_lines(self, start, sectors):
        self.session.set_timing_lines(start, sectors)
        self.table.refresh()
        self._select_default()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    interpolate = "--interp" in argv  # off by default; the C++ fit diverges on long sessions
    paths = [a for a in argv if not a.startswith("-")] or [DEFAULT_SAMPLE]
    app = QApplication(sys.argv)
    window = StudioWindow(paths, interpolate=interpolate)
    window.show()
    return app.exec()
