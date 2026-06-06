"""PlotsView: speed-vs-distance (top) and lap-vs-best delta (bottom), x-linked.

Shows the laps selected in the lap table. A vertical cursor on both plots follows the
video position whenever the currently-playing lap is among those displayed.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .session import fmt_time

# Antialiased path rendering is a major per-repaint cost; the cursor's InfiniteLine.setValue
# re-renders every visible curve each ~30 Hz tick, so keep it OFF for smooth playback.
pg.setConfigOptions(antialias=False)

PALETTE = ["#39a0ed", "#ef476f", "#ffd166", "#06d6a0", "#b388eb", "#ff924c", "#118ab2"]
CURSOR_PEN = pg.mkPen("#ffffff", width=1, style=Qt.DashLine)


class PlotsView(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []

        self.glw = pg.GraphicsLayoutWidget()
        self.p_speed = self.glw.addPlot(row=0, col=0)
        self.p_delta = self.glw.addPlot(row=1, col=0)
        self.p_speed.setLabel("left", "speed (km/h)")
        self.p_speed.showGrid(x=True, y=True, alpha=0.2)
        self.p_speed.addLegend(offset=(8, 8))
        self.p_delta.setLabel("left", "Δ to best (s)")
        self.p_delta.setLabel("bottom", "distance (m)")
        self.p_delta.showGrid(x=True, y=True, alpha=0.2)
        self.p_delta.setXLink(self.p_speed)
        self.p_delta.addLine(y=0, pen=pg.mkPen("#555", width=1))

        self.cur_speed = pg.InfiniteLine(angle=90, movable=False, pen=CURSOR_PEN)
        self.cur_delta = pg.InfiniteLine(angle=90, movable=False, pen=CURSOR_PEN)
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)
        self.p_speed.addItem(self.cur_speed)
        self.p_delta.addItem(self.cur_delta)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.glw)

    def set_laps(self, lap_ids):
        self._lap_ids = list(lap_ids)
        self.refresh()

    def refresh(self):
        for plot, curve in self._curves:
            plot.removeItem(curve)
        self._curves = []

        # Re-enable autorange so the new selection's curves are fit before we freeze it again.
        self.p_speed.enableAutoRange()
        self.p_delta.enableAutoRange()

        result = self.session.delta(self._lap_ids)
        if not result:
            self.p_speed.setTitle(None)
            return
        best, speed, delta = result
        labels = [f"lap {lid} {fmt_time(self.session.laps.lap_time(lid))}"
                  + (" ★best" if lid == best else "") for lid in self._lap_ids]
        self.p_speed.setTitle("   ".join(labels) or None)
        for k, lid in enumerate(self._lap_ids):
            color = PALETTE[k % len(PALETTE)]
            pen = pg.mkPen(color, width=2)
            name = f"lap {lid}" + (" (best)" if lid == best else "")
            if lid in speed:
                dist, spd = speed[lid]
                c = self.p_speed.plot(dist, spd, pen=pen, name=name)
                # Distance x-axis is monotonic, so downsampling + clip-to-view is valid and
                # cuts the segments re-rendered on every cursor tick to roughly the visible set.
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_speed, c))
            if lid in delta:
                dd, dl = delta[lid]
                c = self.p_delta.plot(dd, dl, pen=pen)
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_delta, c))

        # Fit each plot to its data once, then freeze autorange: cursor moves (InfiniteLine
        # setValue every tick) must not trigger a range recompute. x is linked, so fitting both
        # axes here covers the shared x range and each plot's own y range. Pan/zoom still works.
        self.glw.scene().update()
        self.p_speed.autoRange()
        self.p_delta.autoRange()
        self.p_speed.disableAutoRange()
        self.p_delta.disableAutoRange()

    def set_cursor_time(self, t: float):
        dist = None
        for lid in self._lap_ids:
            window = self.session.lap_window(lid)
            if window and window[0] <= t <= window[1]:
                dist = self.session.distance_in_lap_at_time(lid, t)
                break
        show = dist is not None
        self.cur_speed.setVisible(show)
        self.cur_delta.setVisible(show)
        if show:
            self.cur_speed.setValue(dist)
            self.cur_delta.setValue(dist)
