"""Editor for the 64 Electribe slices and trigger steps."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.domain import Sample
from e2sample_manager.formats import FormatError
from e2sample_manager.services import AudioService, LibraryService, ServiceError


class SliceEditorDialog(QDialog):
    """Edit all slice descriptors and their 64-step beat assignment."""

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
        self.setWindowTitle(f"Slice-Editor – {sample.metadata.name}")
        self.resize(820, 650)
        layout = QVBoxLayout(self)
        if sample.metadata.stereo:
            warning = QLabel("⚠ Slice-Samples sollten mono sein; Vorschau nutzt beide Kanäle.")
            warning.setStyleSheet("color: #c87800; font-weight: bold")
            layout.addWidget(warning)
        beat_row = QHBoxLayout()
        beat_row.addWidget(QLabel("Beat-Raster"))
        self.beats = QComboBox()
        self.beats.addItems(("Aus Metadaten", "8 Beats", "16 Beats", "32 Beats", "64 Beats"))
        beat_row.addWidget(self.beats)
        beat_row.addStretch()
        layout.addLayout(beat_row)
        self.table = QTableWidget(64, 6)
        self.table.setHorizontalHeaderLabels(
            ("Slice", "Start", "Länge", "Attack", "Amplitude", "Step")
        )
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        preview = QPushButton("Sample-Vorschau")
        stop = QPushButton("Stop")
        actions.addWidget(preview)
        actions.addWidget(stop)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        self._populate()
        self.beats.currentIndexChanged.connect(self._apply_beat_grid)
        preview.clicked.connect(self._preview)
        stop.clicked.connect(audio.stop)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

    def _spin(self, value: int, maximum: int = 0x7FFFFFFF) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, maximum)
        spin.setValue(max(0, value))
        return spin

    def _populate(self) -> None:
        for row, (slice_, step) in enumerate(
            zip(self.sample.metadata.slices, self.sample.metadata.steps, strict=True)
        ):
            number = QTableWidgetItem(str(row + 1))
            number.setFlags(number.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, number)
            self.table.setCellWidget(row, 1, self._spin(slice_.start))
            self.table.setCellWidget(row, 2, self._spin(slice_.length))
            self.table.setCellWidget(row, 3, self._spin(slice_.attack_length))
            self.table.setCellWidget(row, 4, self._spin(slice_.amplitude))
            step_spin = QSpinBox()
            step_spin.setRange(-128, 127)
            step_spin.setValue(step)
            self.table.setCellWidget(row, 5, step_spin)

    def _apply_beat_grid(self, index: int) -> None:
        if index == 0:
            return
        count = (8, 16, 32, 64)[index - 1]
        for row in range(64):
            self.table.cellWidget(row, 5).setValue(row + 1 if row < count else 0)

    def _preview(self) -> None:
        try:
            self.audio.play_region(self.sample)
        except (ServiceError, FormatError) as exc:
            QMessageBox.warning(self, "Wiedergabe", str(exc))

    def _save(self) -> None:
        slices = copy.deepcopy(self.sample.metadata.slices)
        steps: list[int] = []
        for row, slice_ in enumerate(slices):
            slice_.start = self.table.cellWidget(row, 1).value()
            slice_.length = self.table.cellWidget(row, 2).value()
            slice_.attack_length = self.table.cellWidget(row, 3).value()
            slice_.amplitude = self.table.cellWidget(row, 4).value()
            steps.append(self.table.cellWidget(row, 5).value())
        try:
            self.service.update_metadata(self.sample.metadata.slot, slices=slices, steps=steps)
        except (ServiceError, FormatError) as exc:
            QMessageBox.critical(self, "Slice-Daten ungültig", str(exc))
            return
        self.changed.emit()
        self.accept()
