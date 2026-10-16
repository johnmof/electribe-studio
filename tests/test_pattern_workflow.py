import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import AudioData, LibraryService, create_sample
from e2sample_manager.ui.pattern_editor import PatternEditor, note_name


def make_editor(qtbot):
    sample = create_sample(
        AudioData(np.ones((64, 1), dtype=np.int16), 44100), slot=501, name="Deep Kick"
    )
    service = LibraryService(E2SAllFile([sample]))
    service.new_pattern()
    editor = PatternEditor(service)
    qtbot.addWidget(editor)
    editor.resize(1200, 700)
    editor.show()
    return service, editor


def test_copy_paste_preserves_target_reserved_bytes_and_other_parts(qtbot, tmp_path):
    service, editor = make_editor(qtbot)
    p = service.patterns
    p.set_step(0, 0, 0, on=True, gate=100, velocity=96, trigger=True, notes=[60, 64, 67, -1])
    source = p.step_base(0, 0, 0)
    target = p.step_base(0, 15, 48)
    p.data[source + 8 : source + 12] = b"ABCD"
    p.data[target + 8 : target + 12] = b"WXYZ"
    editor.refresh()
    editor.copy_bar()
    editor.bar.setCurrentIndex(3)
    editor.grid.setCurrentCell(15, 0)
    before = p.to_bytes()
    editor.paste_bar()
    assert p.step(0, 15, 48) == p.step(0, 0, 0)
    assert p.to_bytes()[target + 8 : target + 12] == b"WXYZ"
    allowed = {p.step_base(0, 15, s) + offset for s in range(48, 64) for offset in range(8)}
    assert all(
        a == b or i in allowed for i, (a, b) in enumerate(zip(before, p.to_bytes(), strict=True))
    )
    service.save_patterns(tmp_path / "edited.e2spat")
    assert PatternFile.load(tmp_path / "edited.e2spat").step(0, 15, 48)["notes"] == [60, 64, 67, -1]
    assert service.undo()
    assert service.patterns.to_bytes() == before
    assert not service.can_undo and not service.sample_dirty
    assert service.redo()


def test_shift_wrap_and_transpose_boundaries_are_safe(qtbot, monkeypatch):
    service, editor = make_editor(qtbot)
    service.patterns.set_step(
        0, 0, 15, on=True, gate=72, velocity=100, trigger=True, notes=[60, 64, 67, -1]
    )
    editor.shift_bar(1)
    assert service.patterns.step(0, 0, 0)["on"]
    editor.transpose_bar(1)
    assert service.patterns.step(0, 0, 0)["notes"] == [61, 65, 68, -1]
    editor.shift_bar(-1)
    assert service.patterns.step(0, 0, 15)["notes"] == [61, 65, 68, -1]
    service.patterns.set_step(
        0, 0, 15, on=True, gate=72, velocity=100, trigger=True, notes=[127, -1, -1, -1]
    )
    before = service.patterns.to_bytes()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args))
    editor.transpose_bar(1)
    assert warned and service.patterns.to_bytes() == before


def test_grid_mouse_keyboard_sample_names_and_last_bar(qtbot):
    service, editor = make_editor(qtbot)
    editor.mouse_mode.setCurrentIndex(1)
    assert "Deep Kick" in editor.grid.verticalHeaderItem(0).text()
    cell = editor.grid.visualItemRect(editor.grid.item(0, 0)).center()
    qtbot.mouseClick(editor.grid.viewport(), Qt.LeftButton, pos=cell)
    assert service.patterns.step(0, 0, 0)["on"]
    assert editor.grid.item(0, 0).text() == "C4"
    qtbot.keyClick(editor.grid, Qt.Key_Space)
    assert not service.patterns.step(0, 0, 0)["on"]
    qtbot.mouseClick(editor.bar_buttons[3], Qt.LeftButton)
    assert editor.bar.currentIndex() == 3
    qtbot.mouseClick(editor.bar_buttons[3], Qt.LeftButton)
    assert editor.bar_buttons[3].isChecked()
    editor.grid.setCurrentCell(15, 15)
    editor.notes[0].setValue(127)
    editor.on.setChecked(True)
    editor.apply_step()
    assert service.patterns.step(0, 15, 63)["notes"][0] == 127
    assert editor.notes_caption.text() == "G9"
    assert note_name(0) == "C-1" and note_name(60) == "C4"


def test_length_limits_navigation_without_deleting_hidden_bars(qtbot):
    service, editor = make_editor(qtbot)
    editor.bar.setCurrentIndex(3)
    hidden = service.patterns.step(0, 0, 63)
    editor.length.setValue(2)
    editor.apply_info()
    assert editor.bar.currentIndex() == 0
    assert not editor.bar_buttons[2].isEnabled()
    assert service.patterns.step(0, 0, 63) == hidden
