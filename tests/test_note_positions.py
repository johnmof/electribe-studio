import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import LibraryService
from e2sample_manager.ui.pattern_editor import PatternEditor


def pattern():
    result = PatternFile.new()
    result.set_step(0, 0, 1, on=True, gate=83, velocity=117, trigger=True, notes=[60, 64, 67, 72])
    return result


def test_step_move_keeps_chord_dynamics_reserved_and_roundtrip(tmp_path):
    service = LibraryService()
    service.patterns = pattern()
    p = service.patterns
    source, target = p.step_base(0, 0, 1), p.step_base(0, 0, 18)
    p.data[source + 8 : source + 12] = b"ABCD"
    p.data[target + 8 : target + 12] = b"WXYZ"
    before = p.to_bytes()
    original = p.step(0, 0, 1)
    service.edit_pattern(lambda doc: doc.move_step(0, 0, 1, 18))
    assert p.step(0, 0, 18) == original
    assert p.step(0, 0, 1)["notes"] == [-1] * 4
    allowed = set(range(source, source + 8)) | set(range(target, target + 8))
    assert all(
        a == b or i in allowed for i, (a, b) in enumerate(zip(before, p.to_bytes(), strict=True))
    )
    service.save_patterns(tmp_path / "moved.e2spat")
    assert PatternFile.load(tmp_path / "moved.e2spat").step(0, 0, 18) == original
    service.undo()
    assert service.patterns.to_bytes() == before and not service.can_undo
    service.redo()
    assert service.patterns.step(0, 0, 18) == original


def test_invalid_targets_and_ties_never_change_pattern():
    for case in ("occupied", "empty", "length", "last_step", "tie", "incoming_tie", "out"):
        p = pattern()
        source, target = 1, 4
        if case == "occupied":
            p.set_step(0, 0, target, **p.step(0, 0, source))
        elif case == "empty":
            source = 3
        elif case == "length":
            p.set_info(0, name="Short", tempo=120, swing=0, length=1)
            target = 18
        elif case == "last_step":
            p.set_part(0, 0, slot=501, level=100, pan=0, mute=False, last_step=4)
        elif case == "tie":
            p.set_step(0, 0, source, **dict(p.step(0, 0, source), gate=127))
        elif case == "incoming_tie":
            p.set_step(0, 0, target - 1, **dict(p.step(0, 0, source), gate=127))
        elif case == "out":
            target = 64
        before = p.to_bytes()
        with pytest.raises(ValueError):
            p.move_step(0, 0, source, target)
        assert p.to_bytes() == before


def editor_for(qtbot):
    service = LibraryService()
    service.patterns = pattern()
    editor = PatternEditor(service)
    qtbot.addWidget(editor)
    editor.resize(1200, 700)
    editor.show()
    return service, editor


def test_select_and_alt_arrows_across_bars(qtbot):
    service, editor = editor_for(qtbot)
    cell = editor.grid.visualItemRect(editor.grid.item(0, 1)).center()
    qtbot.mouseClick(editor.grid.viewport(), Qt.LeftButton, pos=cell)
    assert service.patterns.step(0, 0, 1)["on"]
    editor.move_current_step(15)
    qtbot.keyClick(editor.grid, Qt.Key_Right, modifier=Qt.AltModifier)
    assert editor.bar.currentIndex() == 1 and editor.step_index == 16
    assert service.patterns.step(0, 0, 16)["notes"] == [60, 64, 67, 72]
    editor.target_step.setValue(32)
    editor.move_current_step(editor.target_step.value() - 1)
    assert service.patterns.step(0, 0, 31)["on"]


def test_drag_a_step_without_toggling_it(qtbot, monkeypatch):
    service, editor = editor_for(qtbot)
    start = editor.grid.visualItemRect(editor.grid.item(0, 1)).center()
    end = editor.grid.visualItemRect(editor.grid.item(0, 4)).center()
    qtbot.mousePress(editor.grid.viewport(), Qt.LeftButton, pos=start)
    qtbot.mouseMove(editor.grid.viewport(), end)
    qtbot.mouseRelease(editor.grid.viewport(), Qt.LeftButton, pos=end)
    assert not service.patterns.step(0, 0, 1)["on"]
    assert service.patterns.step(0, 0, 4)["notes"] == [60, 64, 67, 72]
    before = service.patterns.to_bytes()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    editor.drag_step(0, 4, 1, 4)
    assert warnings and service.patterns.to_bytes() == before
