"""Main window for the native German Qt application."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from e2sample_manager.domain import HACKTRIBE_PROFILE, KORG_PROFILE
from e2sample_manager.formats import FormatError
from e2sample_manager.services import AudioService, LibraryService, ServiceError

from .dialogs import HacktribeArrangeDialog, ImportOptionsDialog
from .move_dialog import MoveRangeDialog, MoveSelectionDialog
from .pattern_editor import PatternEditor
from .sample_table import SampleTableWidget
from .slice_editor import SliceEditorDialog
from .waveform_editor import WaveformEditorDialog

AUDIO_FILTER = "Audiodateien (*.wav *.wave *.aif *.aiff *.mp3)"
ALL_FILTER = "Electribe-Bibliothek (*.all);;Alle Dateien (*)"
MEMORY_WARNING = 24 * 1024 * 1024


class MainWindow(QMainWindow):
    """Top-level application window using the service layer exclusively."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        library_service: LibraryService | None = None,
        audio_service: AudioService | None = None,
    ) -> None:
        super().__init__()
        self.service = library_service or LibraryService()
        self.audio = audio_service or AudioService()
        self.settings = QSettings("e2sample-manager", "e2sample-manager")
        self.setWindowTitle("e2sample-manager")
        self.resize(1250, 850)
        self.setMinimumSize(1050, 720)
        self.table = SampleTableWidget(self.service)
        self.pattern_editor = PatternEditor(self.service)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.table, "01   Samples")
        self.tabs.addTab(self.pattern_editor, "02   Patterns")
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 16, 22, 12)
        title = QLabel("Electribe Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel(
            "Samples ordnen. Patterns gestalten. Für Electribe 2 Sampler & Hacktribe."
        )
        subtitle.setObjectName("subtitle")
        self.brand_title, self.brand_subtitle = title, subtitle
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.profile_label = QLabel()
        self.profile_label.setObjectName("sectionTitle")
        layout.addWidget(self.profile_label)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self._create_status()
        self.table.model.changed.connect(self._changed)
        self.table.model.error.connect(lambda message: self._show_error("Bearbeiten", message))
        self.table.audioDropped.connect(self.import_audio)
        self.table.selectionChanged.connect(self._update_actions)
        self.table.view.moveRequested.connect(self.move_selection_dropped)
        self.table.view.addAction(self.move_selected_action)
        self.table.view.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.pattern_editor.changed.connect(self._changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._refresh()
        if path is not None:
            self.open_path(path)

    def _action(
        self,
        text: str,
        slot: Callable,
        shortcut: QKeySequence.StandardKey | str | None = None,
        *,
        name: str,
    ) -> QAction:
        action = QAction(text, self)
        action.setObjectName(name)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return action

    def _create_actions(self) -> None:
        self.new_action = self._action("&Neu", self.new_library, QKeySequence.New, name="actionNew")
        self.open_action = self._action(
            "Ö&ffnen …", self.open_dialog, QKeySequence.Open, name="actionOpen"
        )
        self.save_action = self._action(
            "&Speichern", self.save, QKeySequence.Save, name="actionSave"
        )
        self.save_as_action = self._action(
            "Speichern &unter …", self.save_as, QKeySequence.SaveAs, name="actionSaveAs"
        )
        self.merge_action = self._action(
            ".all zusammenführen …", self.merge_all, name="actionMerge"
        )
        self.import_action = self._action(
            "Audio importieren …", self.choose_import, "Ctrl+I", name="actionImport"
        )
        self.export_action = self._action(
            "Auswahl als WAV exportieren …", self.export_one, name="actionExport"
        )
        self.export_all_action = self._action(
            "Alle als WAV exportieren …", self.export_all, name="actionExportAll"
        )
        self.quit_action = self._action(
            "&Beenden", self.close, QKeySequence.Quit, name="actionQuit"
        )
        self.undo_action = self._action(
            "&Rückgängig", self.undo, QKeySequence.Undo, name="actionUndo"
        )
        self.redo_action = self._action(
            "Wieder&holen", self.redo, QKeySequence.Redo, name="actionRedo"
        )
        self.delete_action = self._action(
            "Löschen", self.delete_selected, QKeySequence.Delete, name="actionDelete"
        )
        self.duplicate_action = self._action(
            "Duplizieren", self.duplicate_selected, "Ctrl+D", name="actionDuplicate"
        )
        self.play_action = self._action("Abspielen", self.play_selected, "Space", name="actionPlay")
        self.stop_action = self._action("Stop", self.audio.stop, "Esc", name="actionStop")
        self.waveform_action = self._action(
            "Wellenform bearbeiten …", self.edit_waveform, "Ctrl+E", name="actionWaveform"
        )
        self.slice_action = self._action(
            "Slices bearbeiten …", self.edit_slices, name="actionSlices"
        )
        self.hacktribe_action = self._action(
            "Für Hacktribe ab Slot 501 anordnen …",
            self.arrange_hacktribe,
            name="actionHacktribeArrange",
        )
        self.move_action = self._action(
            "Bereich verschieben …", self.move_range, "Ctrl+M", name="actionMoveRange"
        )
        self.move_selected_action = self._action(
            "Auswahl verschieben …", self.move_selected, "Ctrl+Shift+M", name="actionMoveSelected"
        )
        self.mono_action = self._action("Auswahl zu Mono", self.convert_mono, name="actionMono")
        self.normalize_action = self._action(
            "Auswahl normalisieren (−1 dB)", self.normalize_selected, name="actionNormalize"
        )
        self.duplicates_action = self._action(
            "Identische Audiodaten finden …", self.find_duplicates, name="actionDuplicates"
        )
        self.fade_action = self._action("Ein-/Ausblenden …", self.fade_selected, name="actionFade")
        self.usage_action = self._action(
            "Sample-Verwendung anzeigen …", self.show_sample_usage, name="actionUsage"
        )
        self.replace_action = self._action(
            "Sample ersetzen …", self.replace_sample, name="actionReplace"
        )
        self.profile_group = QActionGroup(self)
        self.profile_group.setExclusive(True)
        self.korg_profile_action = QAction("KORG", self, checkable=True)
        self.hack_profile_action = QAction("Hacktribe", self, checkable=True)
        self.korg_profile_action.setObjectName("actionProfileKorg")
        self.hack_profile_action.setObjectName("actionProfileHacktribe")
        self.profile_group.addAction(self.korg_profile_action)
        self.profile_group.addAction(self.hack_profile_action)
        self.korg_profile_action.triggered.connect(lambda: self._set_profile(KORG_PROFILE))
        self.hack_profile_action.triggered.connect(lambda: self._set_profile(HACKTRIBE_PROFILE))

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        file_menu.addActions(
            (self.new_action, self.open_action, self.save_action, self.save_as_action)
        )
        file_menu.addSeparator()
        file_menu.addActions((self.merge_action, self.import_action))
        file_menu.addActions((self.export_action, self.export_all_action))
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)
        edit_menu = self.menuBar().addMenu("&Bearbeiten")
        edit_menu.addActions((self.undo_action, self.redo_action))
        edit_menu.addSeparator()
        edit_menu.addActions((self.delete_action, self.duplicate_action, self.move_action))
        edit_menu.addAction(self.move_selected_action)
        sample_menu = self.menuBar().addMenu("&Sample")
        sample_menu.addActions(
            (self.play_action, self.stop_action, self.waveform_action, self.slice_action)
        )
        sample_menu.addActions((self.mono_action, self.replace_action))
        sample_menu.addSeparator()
        sample_menu.addActions((self.normalize_action, self.duplicates_action))
        sample_menu.addActions((self.fade_action, self.usage_action))
        profile_menu = self.menuBar().addMenu("&Profil")
        profile_menu.addActions((self.korg_profile_action, self.hack_profile_action))
        profile_menu.addSeparator()
        profile_menu.addAction(self.hacktribe_action)
        pattern_menu = self.menuBar().addMenu("&Patterns")
        for label, handler in (
            ("Öffnen …", self.pattern_editor.open_file),
            ("Speichern …", self.pattern_editor.save_file),
            ("Neues Pattern", self.pattern_editor.new_pattern),
        ):
            action = pattern_menu.addAction(label)
            action.triggered.connect(handler)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Hauptwerkzeuge", self)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)
        toolbar.addActions((self.new_action, self.open_action, self.save_action))
        toolbar.addSeparator()
        toolbar.addActions((self.import_action, self.move_selected_action))
        toolbar.addSeparator()
        toolbar.addActions((self.undo_action, self.redo_action, self.delete_action))
        toolbar.addSeparator()
        toolbar.addActions((self.play_action, self.stop_action))
        row = QHBoxLayout()
        for action in (
            self.waveform_action,
            self.slice_action,
            self.mono_action,
            self.replace_action,
            self.export_action,
        ):
            button = QPushButton(action.text())
            button.clicked.connect(action.trigger)
            action.changed.connect(lambda a=action, b=button: b.setEnabled(a.isEnabled()))
            row.addWidget(button)
        row.addStretch()
        self.table.layout().insertLayout(1, row)

    def _create_status(self) -> None:
        self.count_label = QLabel()
        self.memory_label = QLabel()
        self.memory_bar = QProgressBar()
        self.memory_bar.setMaximumWidth(180)
        self.memory_bar.setTextVisible(False)
        self.statusBar().addPermanentWidget(self.count_label)
        self.statusBar().addPermanentWidget(self.memory_label)
        self.statusBar().addPermanentWidget(self.memory_bar)

    def _last_dir(self, key: str) -> str:
        return str(self.settings.value(f"dirs/{key}", str(Path.home())))

    def _remember_dir(self, key: str, path: str | Path) -> None:
        self.settings.setValue(f"dirs/{key}", str(Path(path).resolve().parent))

    def _guard(self, title: str, operation: Callable[[], object]) -> bool:
        try:
            operation()
            return True
        except (ServiceError, FormatError, OSError, ValueError) as exc:
            self._show_error(title, str(exc))
            return False

    def _show_error(self, title: str, message: str) -> None:
        logging.getLogger("electribe").error("%s: %s", title, message, exc_info=True)
        QMessageBox.critical(self, title, message or "Unbekannter Fehler.")

    def _confirm_discard(self) -> bool:
        if not self.service.dirty:
            return True
        answer = QMessageBox.warning(
            self,
            "Ungespeicherte Änderungen",
            "Samples oder Patterns wurden geändert. Vor dem Fortfahren speichern?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            if self.service.sample_dirty and not self.save_samples():
                return False
            if self.service.pattern_dirty and not self.pattern_editor.save_file():
                return False
        return True

    def new_library(self) -> None:
        if self.tabs.currentIndex() == 1:
            self.pattern_editor.new_pattern()
            return
        if self._confirm_discard():
            self.audio.stop()
            self.service.new(profile=self.service.profile)
            self._refresh()

    def open_dialog(self) -> None:
        if self.tabs.currentIndex() == 1:
            self.pattern_editor.open_file()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Bibliothek öffnen", self._last_dir("all"), ALL_FILTER
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        if not self._confirm_discard():
            return False
        self.audio.stop()
        result = self._guard("Öffnen fehlgeschlagen", lambda: self.service.open(path))
        if result:
            self._remember_dir("all", path)
            self._refresh()
        return result

    def save(self) -> bool:
        if self.tabs.currentIndex() == 1:
            return self.pattern_editor.save_file()
        return self.save_samples()

    def save_samples(self) -> bool:
        if self.service.path is None:
            return self.save_samples_as()
        result = self._guard("Speichern fehlgeschlagen", self.service.save)
        if result:
            self.statusBar().showMessage("Bibliothek gespeichert.", 3000)
            self._refresh()
        return result

    def move_range(self) -> None:
        dialog = MoveRangeDialog(self.service, self.table.selected_slots(), self)
        if dialog.exec() == QDialog.Accepted and self._guard(
            "Verschieben fehlgeschlagen", lambda: self.service.move_range(*dialog.values())
        ):
            self._refresh()
            self.statusBar().showMessage(
                "Bereich verschoben. Geänderte Samples und Patterns speichern.", 7000
            )

    def convert_mono(self) -> None:
        slots = self.table.selected_slots()
        if slots and self._guard("Mono-Konvertierung", lambda: self.service.convert_mono(slots)):
            self._refresh()

    def move_selected(self) -> None:
        self.move_selection_dropped(self.table.selected_slots(), -1)

    def move_selection_dropped(self, slots: list[int], target: int) -> None:
        if not slots:
            return
        dialog = MoveSelectionDialog(
            self.service, slots, self, target=target if target > 0 else None
        )
        if dialog.exec() != QDialog.Accepted:
            return

        def operation() -> None:
            self.audio.stop()
            mapping = self.service.move_selected(slots, dialog.target.value())
            self._refresh()
            self.table.select_slots(mapping.values())
            self.statusBar().showMessage(
                f"{len(mapping)} Samples verschoben. Geänderte Samples und Patterns speichern.",
                7000,
            )

        self._guard("Auswahl verschieben", operation)

    def normalize_selected(self) -> None:
        slots = self.table.selected_slots()
        if not slots:
            return

        def operation() -> None:
            count = self.service.normalize_samples(slots)
            self._refresh()
            self.statusBar().showMessage(
                f"{count} Samples auf −1 dB normalisiert. Rückgängig mit Strg+Z.", 7000
            )

        self._guard("Normalisieren", operation)

    def find_duplicates(self) -> None:
        groups = self.service.find_duplicate_audio()
        dialog = QDialog(self)
        dialog.setWindowTitle("Identische Audiodaten")
        dialog.resize(580, 400)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            f"{len(groups)} Gruppen mit identischen Audiodaten.\n"
            "Namen, Loops, Slices und Pattern-Verweise können unterschiedlich sein.\n"
            "Es wird nichts gelöscht."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(
            "\n\n".join(
                "\n".join(
                    f"{slot:03d}  {self.service.require_sample(slot).metadata.name}"
                    for slot in slots
                )
                for slots in groups
            )
            or "Keine identischen Audiodaten gefunden."
        )
        layout.addWidget(report)
        close = QPushButton("Schließen")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def fade_selected(self) -> None:
        slots = self.table.selected_slots()
        if not slots:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Kurze Ein-/Ausblendungen")
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            f"{len(slots)} Samples · linear am eingestellten Wiedergabestart und -ende.\n"
            "0 ms deaktiviert die jeweilige Seite. Kurze Bereiche begrenzen die Dauer;\n"
            "überlappende Fades werden multipliziert. Marker bleiben erhalten.\n"
            "Loop-Übergänge und einzelne Slice-Grenzen werden nicht geglättet.\n"
            "Die Audiodaten werden geändert. Rückgängig mit Strg+Z."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        incoming, outgoing = QDoubleSpinBox(), QDoubleSpinBox()
        for field in (incoming, outgoing):
            field.setRange(0, 100)
            field.setDecimals(1)
            field.setSuffix(" ms")
            field.setValue(5)
        form.addRow("Einblenden", incoming)
        form.addRow("Ausblenden", outgoing)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Anwenden")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        def operation() -> None:
            count = self.service.fade_samples(
                slots, fade_in_ms=incoming.value(), fade_out_ms=outgoing.value()
            )
            self._refresh()
            self.statusBar().showMessage(
                f"{count} Samples ein-/ausgeblendet. Strg+Z: Rückgängig.", 7000
            )

        self._guard("Ein-/Ausblenden", operation)

    def show_sample_usage(self) -> None:
        slots = self.table.selected_slots()
        if not slots:
            return

        def operation() -> None:
            usage = self.service.sample_usage(slots)
            dialog = QDialog(self)
            dialog.setWindowTitle("Sample-Verwendung")
            dialog.resize(680, 450)
            layout = QVBoxLayout(dialog)
            hint = QLabel(
                "Nur die geladene Pattern-Datei wird geprüft, auch stumme/leere Parts.\n"
                "Eine Zuordnung muss nicht hörbar sein. Andere Dateien sind unbekannt."
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
            report = QPlainTextEdit()
            report.setReadOnly(True)
            sections = []
            for slot, references in usage.items():
                title = f"Slot {slot:03d} · {self.service.require_sample(slot).metadata.name}"
                lines = [
                    f"Pattern {ref['pattern']:03d} · {ref['name']} · Part {ref['part']:02d}"
                    + (" · stumm" if ref["mute"] else "")
                    for ref in references
                ]
                sections.append(
                    title + "\n" + "\n".join(lines or ["Keine Zuordnung in der geladenen Datei."])
                )
            report.setPlainText("\n\n".join(sections))
            layout.addWidget(report)
            close = QPushButton("Schließen")
            close.clicked.connect(dialog.accept)
            layout.addWidget(close)
            dialog.exec()

        self._guard("Sample-Verwendung", operation)

    def _usage_warning(self, slots: list[int]) -> str:
        if self.service.patterns is None or not self.service.patterns.sampler:
            return ""
        count = sum(len(refs) for refs in self.service.sample_usage(slots).values())
        return (
            (
                f"\n\n{count} Part-Zuordnungen in der geladenen Pattern-Datei verweisen darauf. "
                "Die Zuordnungen bleiben bestehen; ihr Klang kann sich ändern."
            )
            if count
            else ""
        )

    def replace_sample(self) -> None:
        sample = self.table.current_sample()
        if sample is None:
            return
        warning = self._usage_warning([sample.metadata.slot])
        if (
            warning
            and QMessageBox.question(
                self, "Verwendetes Sample ersetzen", "Sample wirklich ersetzen?" + warning
            )
            != QMessageBox.Yes
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Sample ersetzen", self._last_dir("audio"), AUDIO_FILTER
        )
        if path and self._guard(
            "Sample ersetzen", lambda: self.service.replace(sample.metadata.slot, path)
        ):
            self._refresh()

    def save_as(self) -> bool:
        if self.tabs.currentIndex() == 1:
            return self.pattern_editor.save_file()
        return self.save_samples_as()

    def save_samples_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Bibliothek speichern", self._last_dir("all"), ALL_FILTER
        )
        if not path:
            return False
        if not path.lower().endswith(".all"):
            path += ".all"
        result = self._guard("Speichern fehlgeschlagen", lambda: self.service.save_as(path))
        if result:
            self._remember_dir("all", path)
            self._refresh()
        return result

    def merge_all(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, ".all-Datei zusammenführen", self._last_dir("all"), ALL_FILTER
        )
        if path and self._guard(
            "Zusammenführen fehlgeschlagen", lambda: self.service.merge_all(path)
        ):
            self._remember_dir("all", path)
            self._refresh()

    def choose_import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Audio importieren", self._last_dir("audio"), AUDIO_FILTER
        )
        if paths:
            self.import_audio(paths)

    def import_audio(self, paths: list[str]) -> None:
        if not paths:
            return
        dialog = ImportOptionsDialog(
            first_slot=int(
                self.settings.value("import/first_slot", self.service.profile.user_slot_start)
            ),
            mono=self.settings.value("import/mono", False, type=bool),
            left=float(self.settings.value("import/left", 0.5)),
            right=float(self.settings.value("import/right", 0.5)),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        for key, value in options.items():
            self.settings.setValue(f"import/{key}", value)
        if self._guard(
            "Import fehlgeschlagen",
            lambda: self.service.import_samples(paths, **options),
        ):
            self._remember_dir("audio", paths[0])
            self._refresh()

    def export_one(self) -> None:
        sample = self.table.current_sample()
        if sample is None:
            return
        suggested = str(
            Path(self._last_dir("export"))
            / f"{sample.metadata.slot:04d}_{sample.metadata.name}.wav"
        )
        path, _ = QFileDialog.getSaveFileName(self, "Sample exportieren", suggested, "WAVE (*.wav)")
        if path and self._guard(
            "Export fehlgeschlagen",
            lambda: self.service.export(sample.metadata.slot, path, include_markers=True),
        ):
            self._remember_dir("export", path)
            self.statusBar().showMessage("WAVE exportiert.", 3000)

    def export_all(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Alle Samples exportieren", self._last_dir("export")
        )
        if directory and self._guard(
            "Export fehlgeschlagen",
            lambda: self.service.export_all(directory, include_markers=True),
        ):
            self.settings.setValue("dirs/export", directory)
            self.statusBar().showMessage("Alle Samples exportiert.", 3000)

    def undo(self) -> None:
        if self.service.undo():
            self._refresh()

    def redo(self) -> None:
        if self.service.redo():
            self._refresh()

    def delete_selected(self) -> None:
        slots = self.table.selected_slots()
        if not slots:
            return
        if QMessageBox.question(
            self,
            "Samples löschen",
            f"{len(slots)} ausgewählte Sample(s) löschen?" + self._usage_warning(slots),
        ) == QMessageBox.Yes and self._guard(
            "Löschen fehlgeschlagen", lambda: self.service.delete(slots)
        ):
            self._refresh()

    def duplicate_selected(self) -> None:
        sample = self.table.current_sample()
        if sample and self._guard(
            "Duplizieren fehlgeschlagen", lambda: self.service.duplicate(sample.metadata.slot)
        ):
            self._refresh()

    def play_selected(self) -> None:
        sample = self.table.current_sample()
        if sample:
            self._guard("Wiedergabe fehlgeschlagen", lambda: self.audio.play_full(sample))

    def edit_waveform(self) -> None:
        sample = self.table.current_sample()
        if sample:
            dialog = WaveformEditorDialog(self.service, self.audio, sample, self)
            dialog.changed.connect(self._changed)
            dialog.exec()

    def edit_slices(self) -> None:
        sample = self.table.current_sample()
        if sample:
            dialog = SliceEditorDialog(self.service, self.audio, sample, self)
            dialog.changed.connect(self._changed)
            dialog.exec()

    def _set_profile(self, profile) -> None:
        invalid = [
            sample.metadata.slot
            for sample in self.service.library.samples
            if not profile.accepts_slot(sample.metadata.slot)
        ]
        if invalid:
            self._show_error(
                "Profil nicht anwendbar",
                f"Diese Slots sind für {profile.name} ungültig: {invalid[:12]}",
            )
            self._refresh()
            return
        self.service.set_profile(profile)
        self._refresh()

    def arrange_hacktribe(self) -> None:
        slots = [sample.metadata.slot for sample in self.service.library.samples]
        if HacktribeArrangeDialog(slots, self).exec() == QDialog.Accepted and self._guard(
            "Anordnen fehlgeschlagen", self.service.arrange_hacktribe
        ):
            self._refresh()

    def _changed(self) -> None:
        self._refresh(reset_table=False)

    def _refresh(self, *, reset_table: bool = True) -> None:
        if reset_table:
            self.table.refresh()
        count = len(self.service.library.samples)
        used = self.service.library.memory_size
        self.count_label.setText(f"{count} Sample{'s' if count != 1 else ''}")
        self.memory_label.setText(f"{used / (1024 * 1024):.2f} / 24 MiB")
        self.memory_bar.setRange(0, MEMORY_WARNING)
        self.memory_bar.setValue(min(used, MEMORY_WARNING))
        warning = used > MEMORY_WARNING
        self.memory_label.setStyleSheet("color: #da4453; font-weight: bold" if warning else "")
        self.memory_label.setToolTip(
            "24-MiB-Grenze überschritten!" if warning else "Verwendeter Sample-Speicher"
        )
        self.undo_action.setEnabled(self.service.can_undo)
        self.redo_action.setEnabled(self.service.can_redo)
        self.pattern_editor.refresh()
        ranges = "501–999" if self.service.profile == HACKTRIBE_PROFILE else "19–421 / 501–999"
        self.profile_label.setText(f"{self.service.profile.name.upper()}  ·  SAMPLE-SLOTS {ranges}")
        self.korg_profile_action.setChecked(self.service.profile == KORG_PROFILE)
        self.hack_profile_action.setChecked(self.service.profile == HACKTRIBE_PROFILE)
        self._update_actions()
        path = self.service.path.name if self.service.path else "Unbenannt"
        marker = " *" if self.service.dirty else ""
        self.setWindowTitle(f"{path}{marker} – Electribe Studio")

    def _update_actions(self) -> None:
        samples_active = self.tabs.currentIndex() == 0
        selected = self.table.selected_slots()
        has_current = self.table.current_sample() is not None
        for action in (
            self.export_action,
            self.duplicate_action,
            self.play_action,
            self.waveform_action,
            self.slice_action,
        ):
            action.setEnabled(has_current and samples_active)
        self.delete_action.setEnabled(bool(selected) and samples_active)
        self.mono_action.setEnabled(bool(selected) and samples_active)
        self.normalize_action.setEnabled(bool(selected) and samples_active)
        self.fade_action.setEnabled(bool(selected) and samples_active)
        self.usage_action.setEnabled(bool(selected) and samples_active)
        self.duplicates_action.setEnabled(bool(self.service.library.samples) and samples_active)
        self.replace_action.setEnabled(has_current and samples_active)
        self.move_action.setEnabled(bool(self.service.library.samples) and samples_active)
        self.move_selected_action.setEnabled(bool(selected) and samples_active)
        self.export_all_action.setEnabled(bool(self.service.library.samples))

    def _tab_changed(self, index: int) -> None:
        self.brand_title.setVisible(index == 0)
        self.brand_subtitle.setVisible(index == 0)
        for action in (
            self.import_action,
            self.move_action,
            self.move_selected_action,
            self.delete_action,
            self.play_action,
            self.stop_action,
        ):
            action.setVisible(index == 0)
        self._update_actions()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.audio.close()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.sync()
        event.accept()
