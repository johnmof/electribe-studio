"""Sixteen-part step editor for single patterns and complete banks."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.services import LibraryService, ServiceError

PATTERN_FILTER = "Electribe Patterns (*.e2spat *.e2sallpat *.e2pat *.e2allpat)"


def spin(low: int, high: int, value: int = 0) -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    return box


class PatternEditor(QWidget):
    changed = Signal()

    def __init__(self, service: LibraryService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._loading = False
        self.index = self.part = self.step_index = 0
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        for title, handler in (
            ("Neues Pattern", self.new_pattern),
            ("Patterns öffnen …", self.open_file),
            ("Patterns speichern …", self.save_file),
            ("Einzelpattern exportieren …", self.export_current),
        ):
            button = QPushButton(title)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.file_label = QLabel(
            "Ein Pattern öffnen oder neu anlegen. Auch 250er-Banken sind möglich."
        )
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        self.editor = QWidget()
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.pattern_combo = QComboBox()
        self.pattern_combo.setMinimumWidth(180)
        self.name = QLineEdit()
        self.name.setMaxLength(16)
        self.name.setMaximumWidth(190)
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(20, 300)
        self.tempo.setDecimals(1)
        self.length = spin(1, 4, 4)
        self.swing = spin(-50, 50)
        for label, control in (
            ("Pattern", self.pattern_combo),
            ("Name", self.name),
            ("BPM", self.tempo),
            ("Takte", self.length),
            ("Swing", self.swing),
        ):
            row.addWidget(QLabel(label))
            row.addWidget(control)
        row.addStretch()
        editor_layout.addLayout(row)
        self.name.editingFinished.connect(self.apply_info)
        for widget in (self.tempo, self.length, self.swing):
            widget.editingFinished.connect(self.apply_info)

        bar_row = QHBoxLayout()
        bar_row.addWidget(QLabel("Takt anzeigen"))
        self.bar = QComboBox()
        self.bar.addItems(
            ["1 · Steps 1–16", "2 · Steps 17–32", "3 · Steps 33–48", "4 · Steps 49–64"]
        )
        bar_row.addWidget(self.bar)
        bar_row.addWidget(
            QLabel("Kästchen: Step an/aus · Zelle wählen: Noten und Dynamik unten bearbeiten")
        )
        bar_row.addStretch()
        editor_layout.addLayout(bar_row)

        self.grid = QTableWidget(16, 16)
        self.grid.setMinimumHeight(320)
        self.grid.setSelectionMode(QTableWidget.SingleSelection)
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.verticalHeader().setDefaultSectionSize(24)
        self.grid.setVerticalHeaderLabels([f"Part {n + 1:02d}" for n in range(16)])
        editor_layout.addWidget(self.grid, 1)

        details = QGridLayout()
        self.selection_label = QLabel()
        self.selection_label.setObjectName("sectionTitle")
        details.addWidget(self.selection_label, 0, 0, 1, 10)
        self.slot = spin(1, 999, 501)
        self.level = spin(0, 127, 100)
        self.pan = spin(-63, 63)
        self.last_step = spin(1, 16, 16)
        self.mute = QCheckBox("Stumm")
        self.sample_name = QLabel()
        self.sample_name.setMinimumWidth(120)
        for column, (label, widget) in enumerate(
            (
                ("Sample / OSC", self.slot),
                ("Pegel", self.level),
                ("Pan", self.pan),
                ("Last Step", self.last_step),
            )
        ):
            details.addWidget(QLabel(label), 1, column * 2)
            details.addWidget(widget, 1, column * 2 + 1)
            widget.editingFinished.connect(self.apply_part)
        details.addWidget(self.mute, 1, 8)
        details.addWidget(self.sample_name, 1, 9)
        self.mute.clicked.connect(self.apply_part)
        self.on = QCheckBox("Step an")
        self.trigger = QCheckBox("Trigger")
        self.velocity = spin(0, 127, 100)
        self.gate = spin(0, 127, 72)
        details.addWidget(self.on, 2, 0)
        details.addWidget(self.trigger, 2, 1)
        details.addWidget(QLabel("Velocity"), 2, 2)
        details.addWidget(self.velocity, 2, 3)
        details.addWidget(QLabel("Gate · 127 = Tie"), 2, 4)
        details.addWidget(self.gate, 2, 5)
        self.notes = [spin(-1, 127, -1) for _ in range(4)]
        for column, box in enumerate(self.notes):
            box.setSpecialValueText("Aus")
            box.setToolTip("MIDI-Notennummer 0–127; Aus = kein Ton in diesem Notenplatz")
            details.addWidget(QLabel(f"Note {column + 1}"), 3, column * 2)
            details.addWidget(box, 3, column * 2 + 1)
            box.editingFinished.connect(self.apply_step)
        for box in (self.velocity, self.gate):
            box.editingFinished.connect(self.apply_step)
        self.on.clicked.connect(self.apply_step)
        self.trigger.clicked.connect(self.apply_step)
        clear = QPushButton("Takt dieses Parts leeren")
        clear.clicked.connect(self.clear_bar)
        details.addWidget(clear, 3, 8, 1, 2)
        editor_layout.addLayout(details)
        layout.addWidget(self.editor, 1)
        self.pattern_combo.currentIndexChanged.connect(self.select_pattern)
        self.bar.currentIndexChanged.connect(lambda _: self.refresh())
        self.grid.currentCellChanged.connect(self.select_step)
        self.grid.itemChanged.connect(self.toggle_step)
        self.refresh()

    def run_edit(self, operation) -> None:
        if self._loading:
            return
        try:
            self.service.edit_pattern(operation)
        except (ValueError, OSError, ServiceError) as exc:
            QMessageBox.warning(self, "Pattern bearbeiten", str(exc))
        self.refresh()
        self.changed.emit()

    def confirm_replace(self) -> bool:
        if not self.service.pattern_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Patterns speichern",
            "Ungespeicherte Pattern-Änderungen vor dem Öffnen speichern?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        return answer == QMessageBox.Discard or (answer == QMessageBox.Save and self.save_file())

    def new_pattern(self) -> None:
        if self.confirm_replace():
            self.service.new_pattern()
            self.index = 0
            self.refresh()
            self.changed.emit()

    def open_file(self) -> None:
        if not self.confirm_replace():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Patterns öffnen", "", PATTERN_FILTER)
        if not path:
            return
        try:
            self.service.load_patterns(path)
            self.index = 0
            self.refresh()
            self.changed.emit()
        except (ValueError, OSError, ServiceError) as exc:
            QMessageBox.critical(self, "Pattern-Datei", str(exc))

    def save_file(self) -> bool:
        patterns = self.service.patterns
        if patterns is None:
            return True
        extension = (".e2s" if patterns.sampler else ".e2") + (
            "allpat" if patterns.count > 1 else "pat"
        )
        suggested = str(self.service.pattern_path or Path("Patterns" + extension))
        path, _ = QFileDialog.getSaveFileName(
            self, "Patterns speichern", suggested, f"Electribe Pattern (*{extension})"
        )
        if not path:
            return False
        if not path.lower().endswith(extension):
            path += extension
        try:
            self.service.save_patterns(path)
            self.refresh()
            self.changed.emit()
            return True
        except (ValueError, OSError, ServiceError) as exc:
            QMessageBox.critical(self, "Speichern", str(exc))
            return False

    def export_current(self) -> None:
        if self.service.patterns is None:
            return
        patterns = self.service.patterns
        extension = ".e2spat" if patterns.sampler else ".e2pat"
        path, _ = QFileDialog.getSaveFileName(
            self, "Einzelpattern exportieren", "Pattern" + extension, f"Pattern (*{extension})"
        )
        if path:
            try:
                if not path.lower().endswith(extension):
                    path += extension
                if self.service.path and Path(path).resolve() == self.service.path.resolve():
                    raise ValueError("Die Sample-Bank darf nicht durch ein Pattern ersetzt werden.")
                patterns.extract(self.index).save(path)
            except (ValueError, OSError) as exc:
                QMessageBox.critical(self, "Export", str(exc))

    def select_pattern(self, index: int) -> None:
        if not self._loading and index >= 0:
            self.index = index
            self.refresh()

    def select_step(self, row: int, column: int, *_args) -> None:
        if self._loading or row < 0 or column < 0:
            return
        self.part, self.step_index = row, self.bar.currentIndex() * 16 + column
        self._loading = True
        self.load_details()
        self._loading = False

    def toggle_step(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        part, step = item.row(), self.bar.currentIndex() * 16 + item.column()

        def change(patterns):
            values = patterns.step(self.index, part, step)
            values["on"] = item.checkState() == Qt.Checked
            if values["on"] and all(n < 0 for n in values["notes"]):
                values["notes"][0] = 60
                values["trigger"] = True
            patterns.set_step(self.index, part, step, **values)

        self.run_edit(change)

    def apply_info(self) -> None:
        if self._loading or not self.service.patterns:
            return
        values = dict(
            name=self.name.text(),
            tempo=self.tempo.value(),
            swing=self.swing.value(),
            length=self.length.value(),
        )
        if values != self.service.patterns.info(self.index):
            self.run_edit(lambda patterns: patterns.set_info(self.index, **values))

    def apply_part(self) -> None:
        if self._loading or not self.service.patterns:
            return
        values = dict(
            slot=self.slot.value(),
            level=self.level.value(),
            pan=self.pan.value(),
            mute=self.mute.isChecked(),
            last_step=self.last_step.value(),
        )
        if values != self.service.patterns.part_info(self.index, self.part):
            self.run_edit(lambda patterns: patterns.set_part(self.index, self.part, **values))

    def apply_step(self) -> None:
        if self._loading or not self.service.patterns:
            return
        values = dict(
            on=self.on.isChecked(),
            trigger=self.trigger.isChecked(),
            gate=self.gate.value(),
            velocity=self.velocity.value(),
            notes=[box.value() for box in self.notes],
        )
        if values != self.service.patterns.step(self.index, self.part, self.step_index):
            self.run_edit(
                lambda patterns: patterns.set_step(self.index, self.part, self.step_index, **values)
            )

    def clear_bar(self) -> None:
        def change(patterns):
            for step in range(self.bar.currentIndex() * 16, self.bar.currentIndex() * 16 + 16):
                values = patterns.step(self.index, self.part, step)
                values["on"] = False
                patterns.set_step(self.index, self.part, step, **values)

        self.run_edit(change)

    def load_details(self) -> None:
        patterns = self.service.patterns
        if patterns is None:
            return
        self.selection_label.setText(f"PART {self.part + 1:02d}  /  STEP {self.step_index + 1:02d}")
        part = patterns.part_info(self.index, self.part)
        for key, box in (
            ("slot", self.slot),
            ("level", self.level),
            ("pan", self.pan),
            ("last_step", self.last_step),
        ):
            box.setValue(part[key])
        self.mute.setChecked(part["mute"])
        sample = self.service.library.by_slot(part["slot"])
        self.sample_name.setText(sample.metadata.name if sample else "Kein Sample geladen / Synth")
        step = patterns.step(self.index, self.part, self.step_index)
        self.on.setChecked(step["on"])
        self.trigger.setChecked(step["trigger"])
        self.gate.setValue(step["gate"])
        self.velocity.setValue(step["velocity"])
        for box, note in zip(self.notes, step["notes"], strict=True):
            box.setValue(note)

    def refresh(self) -> None:
        self._loading = True
        patterns = self.service.patterns
        self.editor.setEnabled(patterns is not None)
        if patterns is None:
            self.file_label.setText(
                "Ein Pattern öffnen oder neu anlegen. Auch 250er-Banken sind möglich."
            )
            self.pattern_combo.clear()
            self.grid.clearContents()
            self._loading = False
            return
        self.index = min(self.index, patterns.count - 1)
        self.file_label.setText(
            f"{self.service.pattern_path or 'Neues Pattern'}  ·  "
            f"{patterns.count} Pattern(s)"
            + ("  ·  Ungespeichert" if self.service.pattern_dirty else "  ·  Gespeichert")
        )
        labels = [f"{i + 1:03d}  {patterns.info(i)['name']}" for i in range(patterns.count)]
        if [self.pattern_combo.itemText(i) for i in range(self.pattern_combo.count())] != labels:
            self.pattern_combo.clear()
            self.pattern_combo.addItems(labels)
        self.pattern_combo.setCurrentIndex(self.index)
        info = patterns.info(self.index)
        self.name.setText(info["name"])
        self.tempo.setValue(info["tempo"])
        self.length.setValue(info["length"])
        self.swing.setValue(info["swing"])
        bar = self.bar.currentIndex()
        self.step_index = bar * 16 + self.step_index % 16
        self.grid.setHorizontalHeaderLabels([str(bar * 16 + i + 1) for i in range(16)])
        for part in range(16):
            for col in range(16):
                step = patterns.step(self.index, part, bar * 16 + col)
                item = QTableWidgetItem(
                    str(step["notes"][0]) if step["on"] and step["notes"][0] >= 0 else ""
                )
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if step["on"] else Qt.Unchecked)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(
                    QColor("#1d625c" if step["on"] else ("#222e40" if col % 4 == 0 else "#182231"))
                )
                item.setToolTip(
                    f"Part {part + 1}, Step {bar * 16 + col + 1} · Velocity {step['velocity']}"
                )
                self.grid.setItem(part, col, item)
        self.grid.setCurrentCell(self.part, self.step_index % 16)
        self.load_details()
        self._loading = False
