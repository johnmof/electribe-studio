import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample
from e2sample_manager.ui import MainWindow
from e2sample_manager.ui.sample_table import SampleTableWidget


def library(slots=(912, 913, 915, 930, 932)):
    samples = [
        create_sample(
            AudioData(np.full((16, 1), s, dtype=np.int16), 44100), slot=s, name=f"Name {s}"
        )
        for s in slots
    ]
    result = LibraryService(E2SAllFile(samples))
    result.patterns = PatternFile.new()
    for part, slot in enumerate(slots):
        result.patterns.set_oscillator(0, part, slot)
    return result


def test_sparse_selection_preserves_names_audio_and_pattern_identity(tmp_path):
    service = library()
    before = {s.metadata.slot: (s.metadata.name, s.audio_data) for s in service.library.samples}
    mapping = service.move_selected([932, 912, 930, 915], 940)
    assert mapping == {912: 940, 915: 941, 930: 942, 932: 943}
    assert service.require_sample(913).metadata.name == "Name 913"
    for source, target in mapping.items():
        sample = service.require_sample(target)
        assert (sample.metadata.name, sample.audio_data) == before[source]
    assert [service.patterns.oscillator(0, p) for p in range(5)] == [940, 913, 941, 942, 943]
    service.save_as(tmp_path / "moved.all")
    loaded = E2SAllFile.load(tmp_path / "moved.all")
    for source, target in mapping.items():
        assert (loaded.by_slot(target).metadata.name, loaded.by_slot(target).audio_data) == before[
            source
        ]
    assert service.undo()
    assert service.require_sample(912).metadata.name == "Name 912"
    assert not service.can_undo
    assert service.redo()
    assert service.require_sample(943).metadata.name == "Name 932"


def test_overlapping_selection_and_collisions_are_atomic():
    service = library((912, 915, 916))
    before = service.library.to_bytes()
    for slots, target in (([912], 915), ([999], 940), ([912], 450), ([912, 915], 999), ([], 940)):
        with pytest.raises(ServiceError):
            service.move_selected(slots, target)
        assert service.library.to_bytes() == before
        assert not service.can_undo
    assert service.move_selected([912, 915, 916], 914) == {912: 914, 915: 915, 916: 916}
    assert service.require_sample(914).metadata.name == "Name 912"


def test_open_name_editor_follows_sample_after_reordering(qtbot):
    service = library()
    table = SampleTableWidget(service)
    qtbot.addWidget(table)
    table.view.sortByColumn(1, Qt.DescendingOrder)
    index = table.proxy.mapFromSource(table.model.index(0, 1))
    delegate = table.view.itemDelegate()
    editor = delegate.createEditor(table.view, QStyleOptionViewItem(), index)
    assert isinstance(editor, QLineEdit)
    editor.setText("Same sample")
    service.move_selected([912, 915], 940)
    table.refresh()
    # Even a stale row index must never rename whichever sample now occupies it.
    delegate.setModelData(editor, table.proxy, table.proxy.index(0, 1))
    assert service.require_sample(940).metadata.name == "Same sample"
    assert service.require_sample(941).metadata.name == "Name 915"
    assert service.require_sample(932).metadata.name == "Name 932"
    service.new()
    table.refresh()
    delegate.setModelData(editor, table.proxy, table.proxy.index(0, 1))
    assert not service.library.samples


def test_drag_drop_uses_slot_identity_and_preview(qtbot):
    service = library()
    window = MainWindow(library_service=service)
    qtbot.addWidget(window)
    window.table.view.sortByColumn(1, Qt.DescendingOrder)
    window.table.select_slots([912, 915, 930, 932])
    assert window.table.selected_slots() == [912, 915, 930, 932]
    view = window.table.view
    mime = QMimeData()
    mime.setData(view.SAMPLE_MIME, b"selection")
    view._drag_slots = window.table.selected_slots()
    view._drag_revision = window.table.model.revision

    class Drop:
        accepted = False

        def source(self):
            return view

        def mimeData(self):
            return mime

        def position(self):
            return QPointF(-1, -1)

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    def accept_preview():
        dialog = QApplication.activeModalWidget()
        dialog.target.setValue(940)
        assert "912 → 940   Name 912" in dialog.preview_text.toPlainText()
        dialog.accept()

    event = Drop()
    QTimer.singleShot(0, lambda: QTimer.singleShot(0, accept_preview))
    view.dropEvent(event)
    qtbot.waitUntil(lambda: service.library.by_slot(940) is not None)
    assert event.accepted
    assert window.table.selected_slots() == [940, 941, 942, 943]
    assert service.require_sample(940).metadata.name == "Name 912"
    assert service.require_sample(943).metadata.name == "Name 932"
    # A pending drag from before the document reset is rejected.
    assert not view._internal_drag(event)
    window._confirm_discard = lambda: True
