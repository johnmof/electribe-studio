"""A snapped, per-part melody view using only the Electribe's stored step data."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def pitch_name(note):
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[note % 12]}{note // 12 - 1}"


class PianoGrid(QTableWidget):
    noteSelected = Signal(int, int)
    noteDrawn = Signal(int, int)
    noteRemoved = Signal(int, int)
    noteMoved = Signal(int, int, int, int)

    def __init__(self):
        super().__init__(25, 16)
        self.document_token = None
        self.low = 48
        self.bar = 0
        self.spacing = 1
        self.last_step = 16
        self._pressed = None
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.horizontalHeader().setMinimumSectionSize(24)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(21)
        self.verticalHeader().setMinimumSectionSize(19)
        self.verticalHeader().setFixedWidth(66)
        self.setMinimumHeight(290)
        self.setStyleSheet(
            "QTableWidget::item { padding: 0; } QHeaderView::section { padding: 2px; }"
        )

    def pitch(self, row):
        return self.low + self.rowCount() - 1 - row

    def snapped(self, col):
        return min(range(0, self.last_step, self.spacing), key=lambda c: (abs(c - col), c))

    def occupied(self, row, col):
        item = self.item(row, col)
        return item is not None and bool(item.data(Qt.UserRole))

    def mousePressEvent(self, event):
        cell = self.indexAt(event.position().toPoint())
        self._pressed = None
        if cell.isValid() and event.button() == Qt.LeftButton:
            self._pressed = (
                cell.row(),
                cell.column(),
                self.occupied(cell.row(), cell.column()),
                event.position().toPoint(),
                self.document_token,
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed and self._pressed[2]:
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        self.viewport().unsetCursor()
        cell = self.indexAt(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if not cell.isValid():
            return
        row, col = cell.row(), cell.column()
        step, pitch = self.bar * 16 + col, self.pitch(row)
        if event.button() == Qt.RightButton:
            if self.occupied(row, col):
                self.noteRemoved.emit(step, pitch)
            return
        if pressed is None or pressed[4] != self.document_token:
            return
        source_row, source_col, occupied, position, _ = pressed
        moved = (
            event.position().toPoint() - position
        ).manhattanLength() >= QApplication.startDragDistance()
        if occupied and moved:
            if col < self.last_step:
                self.noteMoved.emit(
                    self.bar * 16 + source_col,
                    self.pitch(source_row),
                    self.bar * 16 + self.snapped(col),
                    pitch,
                )
        elif not moved and col < self.last_step:
            if occupied:
                self.noteSelected.emit(step, pitch)
            else:
                self.noteDrawn.emit(self.bar * 16 + self.snapped(col), pitch)

    def keyPressEvent(self, event):
        row, col = self.currentRow(), self.currentColumn()
        if row >= 0 and col >= 0 and self.occupied(row, col) and event.modifiers() == Qt.NoModifier:
            step, pitch = self.bar * 16 + col, self.pitch(row)
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                self.noteRemoved.emit(step, pitch)
                return
            offsets = {
                Qt.Key_Left: (((step - 1) // self.spacing) * self.spacing - step, 0),
                Qt.Key_Right: ((step // self.spacing + 1) * self.spacing - step, 0),
                Qt.Key_Up: (0, 1),
                Qt.Key_Down: (0, -1),
            }
            if event.key() in offsets:
                time, semitones = offsets[event.key()]
                self.noteMoved.emit(step, pitch, step + time, pitch + semitones)
                return
        super().keyPressEvent(event)


class PianoRoll(QWidget):
    partSelected = Signal(int)
    quantizeRequested = Signal()

    def __init__(self):
        super().__init__()
        self.selected_pitch = 60
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.part = QComboBox()
        self.part.setMinimumContentsLength(16)
        self.part.currentIndexChanged.connect(self.partSelected)
        row.addWidget(self.part, 1)
        row.addWidget(QLabel("Tonbereich"))
        self.octave = QComboBox()
        for low in range(0, 109, 12):
            self.octave.addItem(f"{pitch_name(low)} – {pitch_name(min(low + 24, 127))}", low)
        self.octave.setCurrentIndex(4)
        row.addWidget(self.octave)
        row.addWidget(QLabel("Raster"))
        self.raster = QComboBox()
        for spacing in (1, 2, 4, 8):
            self.raster.addItem(f"{spacing} Step" + ("s" if spacing > 1 else ""), spacing)
        row.addWidget(self.raster)
        layout.addLayout(row)
        self.grid = PianoGrid()
        layout.addWidget(self.grid, 1)
        footer = QHBoxLayout()
        self.quantize_button = QPushButton("Am Raster ausrichten …")
        self.quantize_button.clicked.connect(self.quantizeRequested)
        footer.addWidget(self.quantize_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        layout.addLayout(footer)
        hint = QLabel(
            "Klick: Note setzen · Ziehen: Zeit / Tonhöhe · Pfeile: verschieben · "
            "Rechtsklick / Entf: löschen\n"
            "Bis zu 4 Noten je Step. Gate und Velocity gelten gemeinsam. "
            "Swing / Groove können das Timing am Gerät weiter verändern."
        )
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def load(self, patterns, index, part, bar, selected_step, labels):
        grid = self.grid
        previous = self.part.blockSignals(True)
        if [self.part.itemText(i) for i in range(self.part.count())] != labels:
            self.part.clear()
            self.part.addItems(labels)
        self.part.setCurrentIndex(part)
        self.part.blockSignals(previous)
        grid.low, grid.bar, grid.spacing = self.octave.currentData(), bar, self.raster.currentData()
        grid.document_token = (id(patterns), index, part, bar, grid.low, grid.spacing)
        playable = patterns.playable_steps(index, part)
        grid.last_step = sum(step // 16 == bar for step in playable)
        grid.setRowCount(min(25, 128 - grid.low))
        grid.setHorizontalHeaderLabels([str(bar * 16 + i + 1) for i in range(16)])
        grid.setVerticalHeaderLabels(
            [pitch_name(grid.pitch(row)) for row in range(grid.rowCount())]
        )
        steps = [patterns.step(index, part, bar * 16 + col) for col in range(16)]
        hidden = 0
        for step in steps:
            if step["on"]:
                hidden += sum(
                    n >= 0 and not grid.low <= n < grid.low + grid.rowCount() for n in step["notes"]
                )
        for row in range(grid.rowCount()):
            pitch = grid.pitch(row)
            for col, step in enumerate(steps):
                active = step["on"] and pitch in step["notes"]
                item = QTableWidgetItem(pitch_name(pitch) if active else "")
                item.setData(Qt.UserRole, active)
                enabled = col < grid.last_step
                item.setFlags(
                    (Qt.ItemIsEnabled | Qt.ItemIsSelectable) if enabled else Qt.NoItemFlags
                )
                item.setTextAlignment(Qt.AlignCenter)
                color = (
                    "#277e71"
                    if active
                    else (
                        "#111b28"
                        if not enabled
                        else "#304458"
                        if col % grid.spacing == 0
                        else "#1b293a"
                    )
                )
                item.setBackground(QColor(color))
                item.setForeground(QColor("#effffb"))
                item.setToolTip(
                    f"{pitch_name(pitch)} · MIDI {pitch} · Step {bar * 16 + col + 1}"
                    + (f" · Velocity {step['velocity']} · Gate {step['gate']}" if active else "")
                )
                grid.setItem(row, col, item)
        row = grid.low + grid.rowCount() - 1 - self.selected_pitch
        if 0 <= row < grid.rowCount():
            grid.setCurrentCell(row, selected_step % 16)
        else:
            grid.clearSelection()
            grid.setCurrentCell(-1, -1)
        self.status.setText(
            f"Part {part + 1:02d} · Takt {bar + 1} · {grid.last_step} spielbare Steps"
            + (f" · {hidden} Note(n) außerhalb des Tonbereichs" if hidden else "")
        )
