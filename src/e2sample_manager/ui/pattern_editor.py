"""Sixteen-part step editor for single patterns and complete banks."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.services import LibraryService, ServiceError
from e2sample_manager.ui.piano_roll import PianoRoll

PATTERN_FILTER = "Electribe Patterns (*.e2spat *.e2sallpat *.e2pat *.e2allpat)"


def note_name(value):
    pitches = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return "–" if value < 0 else f"{pitches[value % 12]}{value // 12 - 1}"


class StepDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.fillRect(option.rect, QColor("#162332"))
        rect = option.rect.adjusted(2, 3, -2, -3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(index.data(Qt.BackgroundRole) or QColor("#223247"))
        painter.drawRoundedRect(rect, 4, 4)
        if option.state & QStyle.State_Selected:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#f3c77e"), 2))
            painter.drawRoundedRect(rect, 4, 4)
        if getattr(self.parent(), "_drop_cell", None) == (index.row(), index.column()):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#f3c77e"), 2))
            painter.drawRoundedRect(rect, 4, 4)
        if index.column() % 4 == 0:
            painter.setPen(QColor("#54647b"))
            painter.drawLine(option.rect.topLeft(), option.rect.bottomLeft())
        painter.setPen(
            QColor("#effffb")
            if index.data(Qt.CheckStateRole) in (Qt.Checked, Qt.Checked.value)
            else QColor("#718298")
        )
        font = option.font
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, index.data(Qt.DisplayRole) or "·")
        painter.restore()


class StepGrid(QTableWidget):
    toggleRequested = Signal()
    nudgeRequested = Signal(int)
    stepMoved = Signal(int, int, int, int)

    def __init__(self, *args):
        super().__init__(*args)
        self._press_cell = self._drop_cell = None
        self._moving = self._suppress_click = False
        self.document_token = None

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        item = self.item(index.row(), index.column()) if index.isValid() else None
        self._press_cell = (
            (index.row(), index.column())
            if item is not None
            and item.checkState() == Qt.Checked
            and event.button() == Qt.LeftButton
            else None
        )
        self._press_position = event.position().toPoint()
        self._press_token = self.document_token
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_cell is not None:
            if (
                event.position().toPoint() - self._press_position
            ).manhattanLength() >= QApplication.startDragDistance():
                self._moving = True
            if self._moving:
                index = self.indexAt(event.position().toPoint())
                self._drop_cell = (index.row(), index.column()) if index.isValid() else None
                self.viewport().setCursor(Qt.ClosedHandCursor)
                self.viewport().update()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        source = self._press_cell
        moving = self._moving
        index = self.indexAt(event.position().toPoint())
        self._suppress_click = moving
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._suppress_click = self._moving = False
            self._press_cell = self._drop_cell = None
            self.viewport().unsetCursor()
            self.viewport().update()
        if (
            moving
            and source is not None
            and index.isValid()
            and self._press_token == self.document_token
        ):
            self.stepMoved.emit(source[0], source[1], index.row(), index.column())

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.AltModifier and event.key() in (Qt.Key_Left, Qt.Key_Right):
            self.nudgeRequested.emit(-1 if event.key() == Qt.Key_Left else 1)
            event.accept()
        elif event.key() == Qt.Key_Space and event.modifiers() == Qt.NoModifier:
            self.toggleRequested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


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
        self._bar_clipboard = None
        self.index = self.part = self.step_index = 0
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.file_toolbar = toolbar
        for title, handler in (
            ("Neu", self.new_pattern),
            ("Öffnen …", self.open_file),
            ("Speichern …", self.save_file),
            ("Pattern exportieren …", self.export_current),
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
        self.pattern_combo.setEditable(True)
        self.pattern_combo.setInsertPolicy(QComboBox.NoInsert)
        self.pattern_combo.completer().setFilterMode(Qt.MatchContains)
        self.pattern_combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.pattern_combo.lineEdit().setPlaceholderText("Pattern suchen …")
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
        bar_row.addWidget(QLabel("TAKT"))
        self.bar = QComboBox()
        self.bar.addItems(
            ["1 · Steps 1–16", "2 · Steps 17–32", "3 · Steps 33–48", "4 · Steps 49–64"]
        )
        self.bar.hide()
        self.bar_buttons = []
        for number in range(4):
            button = QPushButton(str(number + 1))
            button.setCheckable(True)
            button.setFixedWidth(46)
            button.clicked.connect(lambda checked=False, n=number: self.select_bar(n))
            bar_row.addWidget(button)
            self.bar_buttons.append(button)
        self.mouse_mode = QComboBox()
        self.mouse_mode.addItems(["Auswählen / Verschieben", "Steps schalten"])
        bar_row.addWidget(self.mouse_mode)
        hint = QLabel("Ziehen · Alt+←/→: 1 Step")
        hint.setObjectName("subtitle")
        bar_row.addWidget(hint)
        bar_row.addStretch()
        body = QSplitter(Qt.Horizontal)
        sequence = QWidget()
        sequence.setMinimumWidth(560)
        sequence_layout = QVBoxLayout(sequence)
        sequence_layout.setContentsMargins(0, 0, 4, 0)
        sequence_layout.addLayout(bar_row)

        self.grid = StepGrid(16, 16)
        self.grid.setMinimumHeight(320)
        self.grid.setSelectionMode(QTableWidget.SingleSelection)
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.verticalHeader().setDefaultSectionSize(24)
        self.grid.verticalHeader().setMinimumSectionSize(22)
        self.grid.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.verticalHeader().setStyleSheet(
            "QHeaderView::section { padding: 2px 6px; font-size: 9pt; }"
        )
        self.grid.verticalHeader().setFixedWidth(165)
        self.grid.horizontalHeader().setMinimumSectionSize(22)
        self.grid.setItemDelegate(StepDelegate(self.grid))
        self.grid.setShowGrid(False)
        self.grid.setFocusPolicy(Qt.StrongFocus)
        self.grid.setVerticalHeaderLabels([f"Part {n + 1:02d}" for n in range(16)])
        self.views = QTabWidget()
        self.views.addTab(self.grid, "Alle Parts")
        self.piano = PianoRoll()
        self.views.addTab(self.piano, "Melodie / Piano-Roll")
        self.views.currentChanged.connect(
            lambda index: (self.mouse_mode.setVisible(index == 0), hint.setVisible(index == 0))
        )
        sequence_layout.addWidget(self.views, 1)
        self.bar_tools_label = QLabel()
        self.bar_tools_label.setObjectName("sectionTitle")
        sequence_layout.addWidget(self.bar_tools_label)
        tools = QHBoxLayout()
        for title, handler in (
            ("Kopieren", self.copy_bar),
            ("Einfügen", self.paste_bar),
            ("← Step", lambda: self.shift_bar(-1)),
            ("Step →", lambda: self.shift_bar(1)),
            ("−1 HT", lambda: self.transpose_bar(-1)),
            ("+1 HT", lambda: self.transpose_bar(1)),
            ("Leeren", self.clear_bar),
        ):
            button = QPushButton(title)
            button.clicked.connect(handler)
            tools.addWidget(button)
            if "HT" in title:
                button.setToolTip("Noten im gewählten Part-Takt um einen Halbton transponieren.")
            if title == "Einfügen":
                self.paste_button = button
        sequence_layout.addLayout(tools)
        self.clipboard_label = QLabel(
            "Step-Daten des gewählten Parts · Motion und Part-Einstellungen bleiben erhalten."
        )
        self.clipboard_label.setObjectName("subtitle")
        self.clipboard_label.setWordWrap(True)
        sequence_layout.addWidget(self.clipboard_label)
        body.addWidget(sequence)

        inspector = QWidget()
        inspector.setMinimumWidth(270)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(10, 0, 0, 0)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("sectionTitle")
        inspector_layout.addWidget(self.selection_label)
        part_group = QGroupBox("Part / Klang")
        part_form = QFormLayout(part_group)
        part_form.setVerticalSpacing(4)
        self.slot = spin(1, 999, 501)
        self.level = spin(0, 127, 100)
        self.pan = spin(-63, 63)
        self.last_step = spin(1, 16, 16)
        self.mute = QCheckBox("Stumm")
        self.sample_name = QLabel()
        self.sample_name.setWordWrap(True)
        self.sample_name.setObjectName("sectionTitle")
        self.sample_picker = QComboBox()
        self.sample_picker.setMinimumContentsLength(12)
        self.sample_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sample_picker.setEditable(True)
        self.sample_picker.setInsertPolicy(QComboBox.NoInsert)
        self.sample_picker.completer().setFilterMode(Qt.MatchContains)
        self.sample_picker.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.sample_picker.lineEdit().setPlaceholderText("Sample suchen …")
        self.sample_picker.currentIndexChanged.connect(self.choose_sample)
        part_form.addRow(self.sample_picker)
        for label, widget in (
            ("Sample / OSC", self.slot),
            ("Pegel", self.level),
            ("Pan", self.pan),
            ("Last Step", self.last_step),
        ):
            part_form.addRow(label, widget)
            widget.editingFinished.connect(self.apply_part)
        part_form.addRow(self.mute)
        inspector_layout.addWidget(part_group)
        self.mute.clicked.connect(self.apply_part)
        step_group = QGroupBox("Step / Noten")
        step_form = QFormLayout(step_group)
        step_form.setVerticalSpacing(4)
        self.on = QCheckBox("Step an")
        self.trigger = QCheckBox("Trigger")
        self.velocity = spin(0, 127, 100)
        self.gate = spin(0, 127, 72)
        step_form.addRow(self.on, self.trigger)
        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(3)
        self.target_step = spin(1, 64, 1)
        self.target_step.setMinimumWidth(48)
        self.target_step.setToolTip("Ziel-Step im ganzen Pattern: 1–64. Takt 2 beginnt bei 17.")
        for title, handler in (("←", lambda: self.nudge_step(-1)),):
            button = QPushButton(title)
            button.setFixedWidth(28)
            button.setStyleSheet("padding: 2px;")
            button.clicked.connect(handler)
            position_layout.addWidget(button)
        position_layout.addWidget(self.target_step)
        for title, handler in (
            ("→", lambda: self.nudge_step(1)),
            ("OK", lambda: self.move_current_step(self.target_step.value() - 1)),
        ):
            button = QPushButton(title)
            button.setFixedWidth(28)
            button.setStyleSheet("padding: 2px;")
            button.clicked.connect(handler)
            position_layout.addWidget(button)
        step_form.addRow("Position", position_row)
        step_form.addRow("Velocity", self.velocity)
        step_form.addRow("Gate · 127 = Tie", self.gate)
        self.notes_caption = QLabel()
        self.notes_caption.setObjectName("sectionTitle")
        self.notes_caption.setWordWrap(True)
        step_form.addRow(self.notes_caption)
        self.notes = [spin(-1, 127, -1) for _ in range(4)]
        note_grid = QGridLayout()
        note_grid.setContentsMargins(0, 0, 0, 0)
        for column, box in enumerate(self.notes):
            box.setSpecialValueText("Aus")
            box.setToolTip("MIDI-Notennummer 0–127; Aus = kein Ton in diesem Notenplatz")
            row, col = divmod(column, 2)
            note_grid.addWidget(QLabel(f"Note {column + 1}"), row, col * 2)
            note_grid.addWidget(box, row, col * 2 + 1)
            box.editingFinished.connect(self.apply_step)
        note_container = QWidget()
        note_container.setLayout(note_grid)
        step_form.addRow(note_container)
        for box in (self.velocity, self.gate):
            box.editingFinished.connect(self.apply_step)
        self.on.clicked.connect(self.apply_step)
        self.trigger.clicked.connect(self.apply_step)
        inspector_layout.addWidget(step_group)
        inspector_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inspector)
        body.addWidget(scroll)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 0)
        body.setSizes([850, 290])
        editor_layout.addWidget(body, 1)
        layout.addWidget(self.editor, 1)
        self.pattern_combo.currentIndexChanged.connect(self.select_pattern)
        self.bar.currentIndexChanged.connect(lambda _: self.refresh())
        self.grid.currentCellChanged.connect(self.select_step)
        self.grid.itemChanged.connect(self.toggle_step)
        self.grid.cellClicked.connect(self.click_step)
        self.grid.toggleRequested.connect(
            lambda: self.click_step(self.grid.currentRow(), self.grid.currentColumn(), force=True)
        )
        self.grid.nudgeRequested.connect(self.nudge_step)
        self.grid.stepMoved.connect(self.drag_step)
        self.piano.partSelected.connect(self.select_melody_part)
        self.piano.octave.currentIndexChanged.connect(lambda _: self.refresh())
        self.piano.raster.currentIndexChanged.connect(lambda _: self.refresh())
        self.piano.grid.noteSelected.connect(self.select_melody_note)
        self.piano.grid.noteDrawn.connect(self.draw_melody_note)
        self.piano.grid.noteRemoved.connect(
            lambda step, pitch: self.draw_melody_note(step, pitch, remove=True)
        )
        self.piano.grid.noteMoved.connect(self.move_melody_note)
        self.piano.quantizeRequested.connect(self.quantize_melody)
        self.refresh()

    def run_edit(self, operation) -> bool:
        if self._loading:
            return False
        success = True
        try:
            self.service.edit_pattern(operation)
        except (ValueError, OSError, ServiceError) as exc:
            success = False
            QMessageBox.warning(self, "Pattern bearbeiten", str(exc))
        self.refresh()
        self.changed.emit()
        return success

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
        self.refresh_piano()
        self._loading = False
        self.grid.viewport().update()

    def refresh_piano(self):
        if self.service.patterns is not None:
            labels = [self.grid.verticalHeaderItem(i).text() for i in range(16)]
            self.piano.load(
                self.service.patterns,
                self.index,
                self.part,
                self.bar.currentIndex(),
                self.step_index,
                labels,
            )

    def select_melody_part(self, part):
        if not self._loading and part >= 0:
            self.part = part
            self.refresh()

    def select_melody_note(self, step, pitch):
        self.piano.selected_pitch = pitch
        self.select_step(self.part, step % 16)

    def draw_melody_note(self, step, pitch, *, remove=False):
        self.step_index = step
        self.piano.selected_pitch = pitch
        self.run_edit(
            lambda patterns: patterns.draw_note(self.index, self.part, step, pitch, remove=remove)
        )

    def move_melody_note(self, source, note, target, pitch):
        if source == target and note == pitch:
            return
        if self.run_edit(
            lambda patterns: patterns.move_note(self.index, self.part, source, note, target, pitch)
        ):
            self.step_index = target
            self.piano.selected_pitch = pitch
            old = self.bar.blockSignals(True)
            self.bar.setCurrentIndex(target // 16)
            self.bar.blockSignals(old)
            self.refresh()

    def quantize_melody(self):
        patterns = self.service.patterns
        if patterns is None:
            return
        bar, spacing = self.bar.currentIndex(), self.piano.raster.currentData()
        try:
            mapping = patterns.quantize_plan(self.index, self.part, bar, spacing)
        except ValueError as exc:
            QMessageBox.warning(self, "Am Raster ausrichten", str(exc))
            return
        if not mapping:
            QMessageBox.information(
                self, "Am Raster ausrichten", "Alle aktiven Steps liegen bereits auf diesem Raster."
            )
            return
        preview = " · ".join(f"{source + 1} → {target + 1}" for source, target in mapping.items())
        answer = QMessageBox.question(
            self,
            "Am Raster ausrichten",
            f"Part {self.part + 1:02d}, Takt {bar + 1}: auf {spacing}-Step-Raster setzen?\n\n"
            f"{preview}\n\nNächstes Rasterfeld; bei Gleichstand das frühere. "
            "Akkorde bleiben zusammen. Motion, Swing und Groove bleiben unverändert.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        if answer == QMessageBox.Ok:
            self.run_edit(lambda doc: doc.quantize_bar(self.index, self.part, bar, spacing))

    def click_step(self, row: int, column: int, *, force=False) -> None:
        if self._loading or row < 0 or column < 0:
            return
        if self.grid._suppress_click or (self.mouse_mode.currentIndex() == 0 and not force):
            return
        item = self.grid.item(row, column)
        if item is not None:
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def nudge_step(self, direction: int) -> None:
        self.move_current_step(self.step_index + direction)

    def move_current_step(self, target: int) -> None:
        source, part = self.step_index, self.part
        if target == source or self.service.patterns is None:
            return
        if self.run_edit(lambda patterns: patterns.move_step(self.index, part, source, target)):
            self.step_index = target
            old = self.bar.blockSignals(True)
            self.bar.setCurrentIndex(target // 16)
            self.bar.blockSignals(old)
            self.refresh()

    def drag_step(self, source_part: int, source_col: int, target_part: int, target_col: int):
        if source_part != target_part:
            QMessageBox.warning(
                self, "Step verschieben", "Bitte innerhalb desselben Parts verschieben."
            )
            return
        self.part = source_part
        self.step_index = self.bar.currentIndex() * 16 + source_col
        self.move_current_step(self.bar.currentIndex() * 16 + target_col)

    def select_bar(self, number: int) -> None:
        self.bar.setCurrentIndex(number)
        for n, button in enumerate(self.bar_buttons):
            button.setChecked(n == self.bar.currentIndex())

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

    def choose_sample(self, index: int) -> None:
        if self._loading or index < 0:
            return
        slot = self.sample_picker.itemData(index)
        if slot is not None:
            self.slot.setValue(slot)
            self.apply_part()

    def _bar_steps(self):
        if self.service.patterns is None:
            return []
        return [
            self.service.patterns.step(self.index, self.part, self.bar.currentIndex() * 16 + i)
            for i in range(16)
        ]

    def _write_bar(self, steps):
        def change(patterns):
            for offset, values in enumerate(steps):
                patterns.set_step(
                    self.index, self.part, self.bar.currentIndex() * 16 + offset, **values
                )

        self.run_edit(change)

    def copy_bar(self) -> None:
        if self.service.patterns is None:
            return
        self._bar_clipboard = self._bar_steps()
        self.clipboard_label.setText(
            f"Kopiert: {self.service.patterns.info(self.index)['name']} · "
            f"Part {self.part + 1:02d} · Takt {self.bar.currentIndex() + 1}. Nur Step-Daten."
        )
        self.paste_button.setEnabled(True)

    def paste_bar(self) -> None:
        if self._bar_clipboard is not None and self.service.patterns is not None:
            self._write_bar(self._bar_clipboard)

    def shift_bar(self, direction: int) -> None:
        steps = self._bar_steps()
        if steps:
            self._write_bar(steps[-direction:] + steps[:-direction])

    def transpose_bar(self, semitones: int) -> None:
        steps = self._bar_steps()
        if not steps:
            return
        for step in steps:
            if any(note >= 0 and not 0 <= note + semitones <= 127 for note in step["notes"]):
                QMessageBox.warning(
                    self, "Transponieren", "Die Noten würden MIDI 0–127 überschreiten."
                )
                return
            step["notes"] = [note + semitones if note >= 0 else -1 for note in step["notes"]]
        self._write_bar(steps)

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
        self.target_step.setRange(1, min(64, patterns.info(self.index)["length"] * 16))
        self.target_step.setValue(self.step_index + 1)
        self.bar_tools_label.setText(
            f"PART {self.part + 1:02d} · TAKT {self.bar.currentIndex() + 1} BEARBEITEN"
        )
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
        self.sample_name.setText(
            sample.metadata.name
            if sample and patterns.sampler
            else f"{'Slot' if patterns.sampler else 'OSC'} {part['slot']:03d}"
        )
        labels = [
            (s.metadata.slot, f"{s.metadata.slot:03d} · {s.metadata.name}")
            for s in sorted(self.service.library.samples, key=lambda item: item.metadata.slot)
        ]
        if [self.sample_picker.itemText(i) for i in range(self.sample_picker.count())] != [
            t for _, t in labels
        ]:
            self.sample_picker.clear()
            for slot, text in labels:
                self.sample_picker.addItem(text, slot)
        self.sample_picker.setVisible(patterns.sampler and bool(labels))
        self.sample_picker.setCurrentIndex(self.sample_picker.findData(part["slot"]))
        step = patterns.step(self.index, self.part, self.step_index)
        self.on.setChecked(step["on"])
        self.trigger.setChecked(step["trigger"])
        self.gate.setValue(step["gate"])
        self.velocity.setValue(step["velocity"])
        for box, note in zip(self.notes, step["notes"], strict=True):
            box.setValue(note)
        self.notes_caption.setText(
            " · ".join(note_name(note) for note in step["notes"] if note >= 0)
            or "Keine Noten · C4 = MIDI 60"
        )

    def refresh(self) -> None:
        self._loading = True
        patterns = self.service.patterns
        self.grid.document_token = (id(patterns), self.index, self.bar.currentIndex())
        self.editor.setEnabled(patterns is not None)
        self.paste_button.setEnabled(patterns is not None and self._bar_clipboard is not None)
        if patterns is None:
            self.file_label.setText(
                "Ein Pattern öffnen oder neu anlegen. Auch 250er-Banken sind möglich."
            )
            self.pattern_combo.clear()
            self.grid.clearContents()
            self._loading = False
            return
        self.index = min(self.index, patterns.count - 1)
        filename = self.service.pattern_path.name if self.service.pattern_path else "Neues Pattern"
        self.file_label.setText(
            f"{filename}  ·  "
            f"{'SAMPLER' if patterns.sampler else 'SYNTH'} · {patterns.count} Pattern(s)"
            + ("  ·  Ungespeichert" if self.service.pattern_dirty else "  ·  Gespeichert")
        )
        self.file_label.setToolTip(str(self.service.pattern_path or ""))
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
        if self.bar.currentIndex() >= info["length"]:
            self.bar.setCurrentIndex(0)
        for n, button in enumerate(self.bar_buttons):
            button.setEnabled(n < info["length"])
            button.setChecked(n == self.bar.currentIndex())
        bar = self.bar.currentIndex()
        self.step_index = bar * 16 + self.step_index % 16
        self.grid.setHorizontalHeaderLabels([str(bar * 16 + i + 1) for i in range(16)])
        for part in range(16):
            part_info = patterns.part_info(self.index, part)
            sample = self.service.library.by_slot(part_info["slot"]) if patterns.sampler else None
            title = (
                sample.metadata.name
                if sample
                else f"{'Slot' if patterns.sampler else 'OSC'} {part_info['slot']:03d}"
            )
            header = QTableWidgetItem(f"{part + 1:02d}  {title}")
            header.setToolTip(
                f"Part {part + 1:02d} · {title} · Slot {part_info['slot']}"
                + (" · stumm" if part_info["mute"] else "")
            )
            header.setForeground(QColor("#76869c" if part_info["mute"] else "#dce9f3"))
            self.grid.setVerticalHeaderItem(part, header)
            for col in range(16):
                step = patterns.step(self.index, part, bar * 16 + col)
                item = QTableWidgetItem(note_name(step["notes"][0]) if step["on"] else "")
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setCheckState(Qt.Checked if step["on"] else Qt.Unchecked)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(
                    QColor(
                        ("#24594f" if part_info["mute"] else "#277e71")
                        if step["on"]
                        else ("#293b50" if col % 4 == 0 else "#1e2d3f")
                    )
                )
                item.setToolTip(
                    f"{title} · Part {part + 1}, Step {bar * 16 + col + 1}\n"
                    f"Velocity {step['velocity']} · Gate {step['gate']}\n"
                    + " · ".join(note_name(note) for note in step["notes"] if note >= 0)
                )
                self.grid.setItem(part, col, item)
        self.grid.setCurrentCell(self.part, self.step_index % 16)
        self.load_details()
        self.refresh_piano()
        self._loading = False
