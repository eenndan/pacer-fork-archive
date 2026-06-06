"""MapView: the track trace with draggable start/sector timing lines + a video marker.

Timing lines are drawn in LOCAL meters (same space as the trace). Each line is two
draggable pyqtgraph TargetItem handles joined by a segment; dragging either handle
re-segments the laps (via the app) live. The red marker tracks the video position and,
when dragged, seeks the video to the nearest telemetry sample.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .plots_view import PALETTE
from .session import Seg

START_COLOR = "#ffd166"
SECTOR_COLOR = "#06d6a0"
TRACE_COLOR = "#8a8f98"
MARKER_COLOR = "#ff5252"


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed):
        self.plot = plot
        self.on_changed = on_changed
        pen = pg.mkPen(color, width=2)
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        self.h1.sigPositionChanged.connect(self._moved)
        self.h2.sigPositionChanged.connect(self._moved)

    def _moved(self, *_):
        p1, p2 = self.h1.pos(), self.h2.pos()
        self.line.setData([p1.x(), p2.x()], [p1.y(), p2.y()])
        self.on_changed()

    def seg(self) -> Seg:
        p1, p2 = self.h1.pos(), self.h2.pos()
        return Seg(p1.x(), p1.y(), p2.x(), p2.y())

    def remove(self):
        for item in (self.line, self.h1, self.h2):
            self.plot.removeItem(item)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)
    seek_requested = Signal(float)  # seconds

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._suppress_marker = False

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.plot.setAspectLocked(True)  # equal aspect -> a true-shape track map
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "x (m)")
        self.plot.setLabel("left", "y (m)")
        self.plot.plot(session.tx, session.ty, pen=pg.mkPen(TRACE_COLOR, width=1))
        self._overlays: list = []  # highlighted selected-lap traces

        self.marker = pg.TargetItem(
            (session.tx[0] if len(session.tx) else 0, session.ty[0] if len(session.ty) else 0),
            size=15, movable=True, pen=pg.mkPen(MARKER_COLOR, width=2),
            brush=pg.mkBrush(255, 82, 82, 110),
        )
        self.plot.addItem(self.marker)
        self.marker.setZValue(10)  # keep the marker above the lap overlays
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        self._rebuild(session.start_line, session.sector_lines)

        add_btn = QPushButton("Add sector")
        reset_btn = QPushButton("Reset sectors")
        add_btn.clicked.connect(self._add_sector)
        reset_btn.clicked.connect(self._reset_sectors)
        row = QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(reset_btn)
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addLayout(row)

    # ----------------------------------------------------------- timing lines
    def _rebuild(self, start: Seg, sectors: list[Seg]):
        for tl in [self._start, *self._sectors]:
            if tl:
                tl.remove()
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit) for s in sectors]

    def _current(self) -> tuple[Seg, list[Seg]]:
        return self._start.seg(), [s.seg() for s in self._sectors]

    def _emit(self):
        start, sectors = self._current()
        self.timing_lines_changed.emit(start, sectors)

    def _add_sector(self):
        start, sectors = self._current()
        sectors.append(self.session.suggest_sector())
        self._rebuild(start, sectors)
        self.timing_lines_changed.emit(start, sectors)

    def _reset_sectors(self):
        start, _ = self._current()
        self._rebuild(start, [])
        self.timing_lines_changed.emit(start, [])

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        if self._suppress_marker:
            return
        p = self.marker.pos()
        i = self.session.nearest_index(p.x(), p.y())
        if i is not None:
            self.seek_requested.emit(float(self.session.tt[i]))

    def set_marker_time(self, t: float):
        i = self.session.index_at_time(t)
        if i is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._suppress_marker = False

    def highlight_laps(self, lap_ids):
        """Overlay the selected laps' traces in their plot colours (matches PlotsView)."""
        for curve in self._overlays:
            self.plot.removeItem(curve)
        self._overlays = []
        for k, lid in enumerate(lap_ids):
            xs, ys = self.session.lap_trace_xy(lid)
            curve = self.plot.plot(xs, ys, pen=pg.mkPen(PALETTE[k % len(PALETTE)], width=3))
            self._overlays.append(curve)
