"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .session import fmt_time

BEST_COLOR = QColor("#06d6a0")
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]


class LapTable(QWidget):
    laps_selected = Signal(object)  # list[int]

    def __init__(self, session):
        super().__init__()
        self.session = session

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self.refresh()

    def refresh(self):
        rows = self.session.lap_rows()
        best = min(range(len(rows)), key=lambda i: rows[i]["time"]) if rows else -1

        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
                str(row["idx"]),
                fmt_time(row["time"]),
                f"{row['dist']:.0f}",
                f"{row['entry']:.1f}",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r == best:
                    item.setForeground(BEST_COLOR)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)

    def select(self, idxs: list[int]):
        self.table.blockSignals(True)
        self.table.clearSelection()
        for r in range(self.table.rowCount()):
            if int(self.table.item(r, 0).text()) in idxs:
                self.table.selectRow(r)
        self.table.blockSignals(False)

    def _on_selection(self):
        ids = sorted({int(self.table.item(idx.row(), 0).text())
                      for idx in self.table.selectionModel().selectedRows()})
        self.laps_selected.emit(ids)
