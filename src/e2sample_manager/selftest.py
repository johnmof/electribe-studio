"""Small packaged-runtime check; no test runner or audio hardware required."""

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .formats.e2s_all import E2SAllFile
from .formats.patterns import PatternFile
from .services import AudioData, LibraryService, create_sample


def run() -> str:
    with TemporaryDirectory(prefix="electribe-studio-") as directory:
        folder = Path(directory)
        sample = create_sample(
            AudioData(np.zeros((128, 2), dtype=np.int16), 44100), slot=501, name="Runtime check"
        )
        service = LibraryService(E2SAllFile([sample]))
        service.new_pattern()
        service.move_range(501, 501, 550)
        service.convert_mono([550])
        service.save_as(folder / "e2sSample.all")
        service.save_patterns(folder / "test.e2spat")
        loaded = E2SAllFile.load(folder / "e2sSample.all")
        assert loaded.by_slot(550).wave.pcm_format[0] == 1
        assert PatternFile.load(folder / "test.e2spat").oscillator(0, 0) == 550
    return (
        "PASS: Windows runtime, sample write/read, mono conversion, pattern write/read, remapping."
    )


def run_ui(bank_path: str | None = None) -> str:
    """Exercise real Qt delegates and document replacement in an isolated process."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QAbstractItemModelTester, QTest
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

    from .ui import MainWindow, WaveformEditorDialog

    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="electribe-ui-") as directory:
        folder = Path(directory)
        samples = [
            create_sample(
                AudioData(np.ones((128, 2), dtype=np.int16), 44100),
                slot=501 + i,
                name=f"Sample {i}",
            )
            for i in range(8)
        ]
        E2SAllFile(samples).save_atomic(folder / "many.all")
        E2SAllFile(samples[:1]).save_atomic(folder / "one.all")
        E2SAllFile().save_atomic(folder / "empty.all")
        window = MainWindow()
        window._confirm_discard = lambda: True
        errors = []
        window._show_error = lambda title, message: errors.append(message)
        window.show()
        testers = [
            QAbstractItemModelTester(
                model, QAbstractItemModelTester.FailureReportingMode.Fatal, window
            )
            for model in (window.table.model, window.table.proxy)
        ]

        def edit_slot(source_slot: int, target: int) -> None:
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            row = next(
                i
                for i in range(window.table.model.rowCount())
                if window.table.model.sample_at(i).metadata.slot == source_slot
            )
            index = window.table.proxy.mapFromSource(window.table.model.index(row, 0))
            window.table.view.setCurrentIndex(index)
            window.table.view.edit(index)
            app.processEvents()
            editor = window.table.view.findChild(QLineEdit)
            assert editor is not None
            editor.setText(str(target))
            QTest.keyClick(editor, Qt.Key_Return)
            app.processEvents()
            assert window.service.require_sample(target).metadata.slot == target

        for _ in range(12):
            for name in ("many.all", "one.all", "empty.all"):
                assert window.open_path(folder / name)
                app.processEvents()
                if not window.table.model.rowCount():
                    continue
                window.table.view.selectRow(window.table.proxy.rowCount() - 1)
                index = window.table.proxy.index(window.table.proxy.rowCount() - 1, 1)
                window.table.view.setCurrentIndex(index)
                window.table.view.edit(index)
                app.processEvents()
                editor = window.table.view.findChild(QLineEdit)
                assert editor is not None
                editor.setText("Edited")
                QTest.keyClick(editor, Qt.Key_Return)
                app.processEvents()
                source = window.table.proxy.mapToSource(window.table.view.currentIndex())
                assert (
                    window.table.model.data(window.table.model.index(source.row(), 1)) == "Edited"
                )
                rate = window.table.model.index(source.row(), 6)
                assert not window.table.model.flags(rate) & Qt.ItemIsEditable
                assert not window.table.model.setData(rate, 0)
                assert not window.table.model.setData(
                    window.table.model.index(source.row(), 5), 999
                )
                window.undo()
                window.redo()
                app.processEvents()
        assert window.open_path(folder / "one.all")
        edit_slot(501, 905)
        window.undo()
        window.redo()
        window.service.delete([905])
        window._refresh()
        assert window.table.model.rowCount() == 0
        assert window.open_path(folder / "one.all")
        item = window.service.require_sample(501)
        dialog = WaveformEditorDialog(window.service, window.audio, item, window)
        dialog.changed.connect(window._changed)
        dialog.show()
        dialog._set_markers(10, 20, 80)
        original_question = QMessageBox.question
        QMessageBox.question = lambda *args: QMessageBox.Yes
        try:
            history = len(window.service._undo)
            dialog._trim()
            assert len(window.service._undo) == history + 1
            assert dialog.end.maximum() == 70
            assert item.metadata.start == 0 and item.metadata.end == 70 * 4
            dialog._set_markers(1, 2, 60)
            app.processEvents()
        finally:
            QMessageBox.question = original_question
        dialog.close()
        if bank_path is not None:
            # The supplied bank is only read; all edited copies stay in this temp folder.
            assert window.open_path(bank_path)
            for _ in range(3):
                if window.service.library.by_slot(905) is not None:
                    window.service.delete([905])
                    window._refresh()
                source_slot = next(
                    s.metadata.slot
                    for s in window.service.library.samples
                    if s.metadata.slot != 905
                )
                original_audio = window.service.require_sample(source_slot).audio_data
                edit_slot(source_slot, 905)
                assert window.service.library.by_slot(source_slot) is None
                assert window.service.require_sample(905).audio_data == original_audio
                window.undo()
                window.redo()
                window.service.save_as(folder / "edited-bank.all")
                loaded = E2SAllFile.load(folder / "edited-bank.all")
                assert loaded.by_slot(905).audio_data == original_audio
                window.service.delete([905])
                window._refresh()
                app.processEvents()
        window.close()
        assert testers and errors  # Invalid tune was rejected without losing the editor.
    return "PASS: 36 bank changes, delegate edits, invalid values, undo/redo and waveform trim." + (
        " Supplied bank: 3 slot-905 edit/save/reload/delete cycles passed." if bank_path else ""
    )
