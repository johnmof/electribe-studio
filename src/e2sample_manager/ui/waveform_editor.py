"""Waveform and Electribe marker editor."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.domain import Sample
from e2sample_manager.services import AudioService, LibraryService, ServiceError


class WaveformWidget(QWidget):
    """Efficient min/max waveform renderer with draggable markers."""

    markersChanged = Signal(int, int, int)
    COLORS = (QColor("#3daee9"), QColor("#e6a700"), QColor("#da4453"))

    def __init__(self, sample: Sample, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sample = sample
        self.zoom = 1.0
        self.offset = 0.0
        self._drag_marker: int | None = None
        self.setMinimumHeight(260)
        self.setMouseTracking(True)

    @property
    def frame_count(self) -> int:
        return len(self.sample.audio_data) // self.sample.wave.pcm_format[2]

    def _visible(self) -> tuple[int, int]:
        count = self.frame_count
        visible = max(1, int(count / self.zoom))
        start = min(int(self.offset * max(0, count - visible)), max(0, count - visible))
        return start, start + visible

    def _markers(self) -> list[int]:
        metadata = self.sample.metadata
        align = self.sample.wave.pcm_format[2]
        return [
            metadata.start // align,
            metadata.loop_start // align,
            (metadata.start + metadata.end) // align,
        ]

    def _x_for_frame(self, frame: int) -> float:
        begin, end = self._visible()
        return (frame - begin) * self.width() / max(1, end - begin)

    def _frame_for_x(self, x: float) -> int:
        begin, end = self._visible()
        return min(
            max(begin + round(x / max(1, self.width()) * (end - begin)), 0), self.frame_count - 1
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        center = self.height() / 2
        painter.setPen(self.palette().mid().color())
        painter.drawLine(0, int(center), self.width(), int(center))
        channels, _, _ = self.sample.wave.pcm_format
        data = np.frombuffer(self.sample.audio_data, dtype="<i2").reshape(-1, channels)
        begin, end = self._visible()
        visible = data[begin:end]
        if len(visible):
            bins = min(self.width(), len(visible))
            edges = np.linspace(0, len(visible), bins + 1, dtype=np.int64)
            painter.setPen(QPen(self.palette().highlight().color(), 1))
            for x in range(bins):
                block = visible[edges[x] : edges[x + 1]]
                if block.size:
                    low = float(block.min()) / 32768.0
                    high = float(block.max()) / 32768.0
                    painter.drawLine(x, int(center - high * center), x, int(center - low * center))
        labels = ("Start", "Loop", "Ende")
        for marker, color, label in zip(self._markers(), self.COLORS, labels, strict=True):
            x = self._x_for_frame(marker)
            painter.setPen(QPen(color, 2))
            painter.drawLine(int(x), 0, int(x), self.height())
            painter.drawText(QPointF(x + 4, 16), label)

    def mousePressEvent(self, event) -> None:
        distances = [
            abs(event.position().x() - self._x_for_frame(frame)) for frame in self._markers()
        ]
        if min(distances, default=99) <= 10:
            self._drag_marker = distances.index(min(distances))

    def mouseMoveEvent(self, event) -> None:
        if self._drag_marker is None:
            return
        markers = self._markers()
        markers[self._drag_marker] = self._frame_for_x(event.position().x())
        markers.sort()
        self.markersChanged.emit(*markers)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_marker = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.zoom = min(128.0, max(1.0, self.zoom * (1.25 if event.angleDelta().y() > 0 else 0.8)))
        self.update()


class WaveformEditorDialog(QDialog):
    """Dialog for marker editing, zero crossing snap, trim and preview."""

    changed = Signal()

    def __init__(
        self,
        service: LibraryService,
        audio: AudioService,
        sample: Sample,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.audio = audio
        self.sample = sample
        self.setWindowTitle(f"Wellenform bearbeiten – {sample.metadata.name}")
        self.resize(900, 520)
        self.waveform = WaveformWidget(sample)
        self.scroll = QSlider(Qt.Horizontal)
        self.scroll.setRange(0, 1000)
        self.snap = QCheckBox("An Nulldurchgängen einrasten")
        self.start = self._spin()
        self.loop = self._spin()
        self.end = self._spin()
        self._load_values()
        form = QFormLayout()
        form.addRow("Start (Frame)", self.start)
        form.addRow("Loop (Frame)", self.loop)
        form.addRow("Ende (Frame)", self.end)
        controls = QHBoxLayout()
        play = QPushButton("Bereich abspielen")
        loop = QPushButton("Loop-Vorschau")
        stop = QPushButton("Stop")
        trim = QPushButton("Auf Bereich trimmen …")
        for button in (play, loop, stop, trim):
            controls.addWidget(button)
        controls.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.waveform)
        layout.addWidget(QLabel("Scroll"))
        layout.addWidget(self.scroll)
        layout.addWidget(self.snap)
        layout.addLayout(form)
        layout.addLayout(controls)
        layout.addWidget(buttons)
        self.waveform.markersChanged.connect(self._set_markers)
        self.scroll.valueChanged.connect(self._scroll)
        for spin in (self.start, self.loop, self.end):
            spin.valueChanged.connect(self._spins_changed)
        play.clicked.connect(lambda: self._play(False))
        loop.clicked.connect(lambda: self._play(True))
        stop.clicked.connect(audio.stop)
        trim.clicked.connect(self._trim)
        buttons.rejected.connect(self.close)

    def _spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, max(0, self.sample.memory_size // self.sample.wave.pcm_format[2] - 1))
        return spin

    def _load_values(self) -> None:
        align = self.sample.wave.pcm_format[2]
        metadata = self.sample.metadata
        self.start.setValue(metadata.start // align)
        self.loop.setValue(metadata.loop_start // align)
        self.end.setValue((metadata.start + metadata.end) // align)

    def _nearest_zero(self, frame: int) -> int:
        if not self.snap.isChecked():
            return frame
        channels, _, _ = self.sample.wave.pcm_format
        data = np.frombuffer(self.sample.audio_data, dtype="<i2").reshape(-1, channels)[:, 0]
        low, high = max(1, frame - 128), min(len(data) - 1, frame + 128)
        candidates = np.where(np.signbit(data[low:high]) != np.signbit(data[low + 1 : high + 1]))[0]
        if not len(candidates):
            return frame
        frames = candidates + low
        return int(frames[np.argmin(abs(frames - frame))])

    def _set_markers(self, start: int, loop: int, end: int) -> None:
        for spin, value in zip((self.start, self.loop, self.end), (start, loop, end), strict=True):
            spin.blockSignals(True)
            spin.setValue(self._nearest_zero(value))
            spin.blockSignals(False)
        self._apply()

    def _spins_changed(self) -> None:
        values = sorted((self.start.value(), self.loop.value(), self.end.value()))
        self._set_markers(*values)

    def _apply(self) -> None:
        align = self.sample.wave.pcm_format[2]
        start, loop, end = self.start.value(), self.loop.value(), self.end.value()
        self.service.update_metadata(
            self.sample.metadata.slot,
            start=start * align,
            loop_start=loop * align,
            end=(end - start) * align,
        )
        self.waveform.update()
        self.changed.emit()

    def _scroll(self, value: int) -> None:
        self.waveform.offset = value / 1000
        self.waveform.update()

    def _play(self, loop: bool) -> None:
        try:
            self.audio.play_region(self.sample, loop=loop)
        except ServiceError as exc:
            QMessageBox.warning(self, "Wiedergabe", str(exc))

    def _trim(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Sample trimmen",
                "Audiodaten außerhalb des Bereichs endgültig entfernen?",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            self.service.trim(self.sample.metadata.slot)
            self._load_values()
            self.waveform.update()
            self.changed.emit()
        except ServiceError as exc:
            QMessageBox.critical(self, "Trimmen fehlgeschlagen", str(exc))
