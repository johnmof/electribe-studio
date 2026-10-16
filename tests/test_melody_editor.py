import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import LibraryService
from e2sample_manager.ui.pattern_editor import PatternEditor


def test_notes_chord_split_limits_preservation_and_file(tmp_path):
    service = LibraryService()
    service.patterns = p = PatternFile.new()
    for step in (0, 4):
        p.data[p.step_base(0, 0, step) + 8 : p.step_base(0, 0, step) + 12] = b"KEEP"
    before = p.to_bytes()
    for note in (60, 64, 67, 72):
        p.draw_note(0, 0, 0, note)
    full = p.to_bytes()
    with pytest.raises(ValueError, match="vier"):
        p.draw_note(0, 0, 0, 74)
    assert p.to_bytes() == full
    p.set_step(0, 0, 0, **dict(p.step(0, 0, 0), gate=83, velocity=117))
    service.edit_pattern(lambda doc: doc.move_note(0, 0, 0, 64, 4, 65))
    assert p.step(0, 0, 0)["notes"] == [60, -1, 67, 72]
    assert p.step(0, 0, 4) == dict(
        on=True, trigger=True, gate=83, velocity=117, notes=[65, -1, -1, -1]
    )
    service.undo()
    assert service.patterns.step(0, 0, 0)["notes"] == [60, 64, 67, 72]
    service.redo()
    p = service.patterns
    p.move_note(0, 0, 4, 65, 4, 66)
    p.draw_note(0, 0, 4, 66, remove=True)
    assert not p.step(0, 0, 4)["on"]
    allowed = {
        offset
        for step in (0, 4)
        for offset in range(p.step_base(0, 0, step), p.step_base(0, 0, step) + 8)
    }
    assert all(
        a == b or i in allowed for i, (a, b) in enumerate(zip(before, p.to_bytes(), strict=True))
    )
    p.save(tmp_path / "melody.e2spat")
    assert PatternFile.load(tmp_path / "melody.e2spat").to_bytes() == p.to_bytes()


def test_quantization_rounding_collision_and_atomic_undo():
    service = LibraryService()
    service.patterns = p = PatternFile.new()
    p.set_step(0, 0, 1, on=True, trigger=True, gate=80, velocity=95, notes=[60, 64, -1, -1])
    p.set_step(0, 0, 7, on=True, trigger=False, gate=90, velocity=115, notes=[67, -1, -1, -1])
    before = p.to_bytes()
    assert p.quantize_plan(0, 0, 0, 4) == {1: 0, 7: 8}
    service.edit_pattern(lambda doc: doc.quantize_bar(0, 0, 0, 4))
    assert p.step(0, 0, 0)["notes"] == [60, 64, -1, -1]
    assert p.step(0, 0, 8)["velocity"] == 115
    changed = {
        offset
        for s in (0, 1, 7, 8)
        for offset in range(p.step_base(0, 0, s), p.step_base(0, 0, s) + 8)
    }
    assert all(
        a == b or i in changed for i, (a, b) in enumerate(zip(before, p.to_bytes(), strict=True))
    )
    service.undo()
    assert service.patterns.to_bytes() == before
    p = service.patterns
    p.draw_note(0, 0, 2, 70)  # At equal distance, snaps earlier to step 1 as well.
    before = p.to_bytes()
    with pytest.raises(ValueError, match="dasselbe Rasterfeld"):
        p.quantize_bar(0, 0, 0, 4)
    assert p.to_bytes() == before


def test_hardware_limits_and_ties_do_not_change_data():
    p = PatternFile.new()
    p.data[p.base(0) + 38] = 2  # Official KORG: 8 Tri; at most 12 steps per bar.
    assert p.playable_steps(0, 0) == [bar * 16 + s for bar in range(4) for s in range(12)]
    before = p.to_bytes()
    with pytest.raises(ValueError):
        p.draw_note(0, 0, 12, 60)
    assert p.to_bytes() == before
    p.set_step(0, 0, 0, on=True, trigger=True, gate=127, velocity=100, notes=[60, -1, -1, -1])
    for operation in (
        lambda: p.move_note(0, 0, 0, 60, 0, 61),
        lambda: p.draw_note(0, 0, 1, 62),
        lambda: p.move_note(0, 0, 0, 60, 4, 128),
    ):
        before = p.to_bytes()
        with pytest.raises(ValueError):
            operation()
        assert p.to_bytes() == before


def test_piano_mouse_pitch_keys_part_and_quantize_preview(qtbot, monkeypatch):
    service = LibraryService()
    service.new_pattern()
    editor = PatternEditor(service)
    qtbot.addWidget(editor)
    editor.resize(1250, 850)
    editor.show()
    editor.views.setCurrentIndex(1)
    editor.piano.raster.setCurrentIndex(2)  # Four stored steps.
    grid = editor.piano.grid
    qtbot.wait(20)

    def point(note, col):
        row = grid.low + grid.rowCount() - 1 - note
        item = grid.item(row, col)
        grid.scrollToItem(item)
        return grid.visualItemRect(item).center()

    qtbot.mouseClick(grid.viewport(), Qt.LeftButton, pos=point(60, 3))
    assert service.patterns.step(0, 0, 4)["notes"][0] == 60
    source, target = point(60, 4), point(64, 8)
    qtbot.mousePress(grid.viewport(), Qt.LeftButton, pos=source)
    qtbot.mouseMove(grid.viewport(), target)
    qtbot.mouseRelease(grid.viewport(), Qt.LeftButton, pos=target)
    assert not service.patterns.step(0, 0, 4)["on"]
    assert service.patterns.step(0, 0, 8)["notes"][0] == 64
    qtbot.keyClick(grid, Qt.Key_Up)
    assert service.patterns.step(0, 0, 8)["notes"][0] == 65
    qtbot.keyClick(grid, Qt.Key_Right)
    assert service.patterns.step(0, 0, 12)["notes"][0] == 65
    qtbot.mouseClick(grid.viewport(), Qt.RightButton, pos=point(65, 12))
    assert not service.patterns.step(0, 0, 12)["on"]
    editor.piano.part.setCurrentIndex(2)
    assert editor.part == 2 and editor.grid.currentRow() == 2
    editor.draw_melody_note(7, 67)
    previews = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: previews.append(args) or QMessageBox.Ok
    )
    qtbot.mouseClick(editor.piano.quantize_button, Qt.LeftButton)
    assert previews and "8 → 9" in previews[0][2]
    assert service.patterns.step(0, 2, 8)["notes"][0] == 67
    service.undo()
    assert service.patterns.step(0, 2, 7)["notes"][0] == 67
