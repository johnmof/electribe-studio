import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import Qt

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample
from e2sample_manager.ui.sample_table import SampleTableWidget


def service():
    audio = AudioData(np.ones((32, 1), dtype=np.int16), 44100)
    result = LibraryService(
        E2SAllFile([create_sample(audio, slot=501), create_sample(audio, slot=905)])
    )
    result.patterns = PatternFile.new()
    return result


def test_delete_905_then_edit_another_sample_to_905(qtbot, tmp_path):
    library = service()
    table = SampleTableWidget(library)
    qtbot.addWidget(table)
    library.delete([905])
    table.refresh()
    assert table.model.setData(table.model.index(0, 0), 905, Qt.EditRole)
    assert library.library.by_slot(501) is None
    assert library.library.by_slot(905) is not None
    assert library.patterns.oscillator(0, 0) == 905
    library.save_as(tmp_path / "moved.all")
    assert E2SAllFile.load(tmp_path / "moved.all").by_slot(905) is not None
    assert library.undo()
    table.refresh()
    assert library.library.by_slot(501) is not None
    assert library.library.by_slot(905) is None
    assert library.redo()
    table.refresh()
    assert table.model.sample_at(0).metadata.slot == 905


def test_slot_edit_collision_and_invalid_slot_leave_rows_editable(qtbot):
    library = service()
    table = SampleTableWidget(library)
    qtbot.addWidget(table)
    errors = []
    table.model.error.connect(errors.append)
    for target in (905, 450, 0, 1021):
        assert not table.model.setData(table.model.index(0, 0), target)
        assert [s.metadata.slot for s in library.library.samples] == [501, 905]
        assert table.model.setData(table.model.index(0, 1), "Still editable")
    assert len(errors) == 4
    assert table.model.setData(table.model.index(0, 0), 904)
    assert table.model.data(table.model.index(0, 0)) == 904


def test_service_distinguishes_current_and_destination_slot():
    library = service()
    with pytest.raises(ServiceError):
        library.update_metadata(501, slot=905)
    library.delete([905])
    assert library.update_metadata(501, slot=905).metadata.slot == 905
