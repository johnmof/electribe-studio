import copy
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample
from e2sample_manager.ui import MainWindow


def library(channels=1, count=20):
    pcm = np.tile([10000, -5000][:channels], (count, 1)).astype(np.int16)
    item = create_sample(AudioData(pcm, 1000), slot=501, name="Fade")
    return LibraryService(E2SAllFile([item]))


@pytest.mark.parametrize("channels", [1, 2])
def test_fades_playback_region_markers_roundtrip_and_undo(channels, tmp_path):
    service = library(channels)
    item = service.require_sample(501)
    align = 2 * channels
    item.metadata.start = 2 * align
    item.metadata.end = 15 * align
    item.metadata.loop_start = 5 * align
    before = item.audio_data
    metadata = copy.deepcopy(item.metadata)
    assert service.fade_samples([501, 501], fade_in_ms=4, fade_out_ms=4) == 1
    pcm = np.frombuffer(item.audio_data, dtype="<i2").reshape(-1, channels)
    assert np.all(pcm[[2, 17]] == 0)
    assert pcm[[0, 1, 5, 6, 14, 18, 19], 0].tolist() == [10000] * 7
    assert pcm[3, 0] == 3333 and pcm[16, 0] == 3333
    if channels == 2:
        assert pcm[3, 1] == -1667
    assert item.metadata == metadata
    service.save_as(tmp_path / "fade.all")
    assert E2SAllFile.load(tmp_path / "fade.all").by_slot(501).audio_data == item.audio_data
    assert service.undo()
    assert service.require_sample(501).audio_data == before
    assert not service.can_undo
    assert service.redo()


def test_tiny_overlapping_fades_and_disabled_sides():
    service = library(count=3)
    assert service.fade_samples([501], fade_in_ms=100, fade_out_ms=100) == 1
    assert np.frombuffer(service.require_sample(501).audio_data, dtype="<i2").tolist() == [
        0,
        2500,
        0,
    ]
    service = library(count=1)
    assert service.fade_samples([501], fade_in_ms=0, fade_out_ms=0) == 0
    assert not service.can_undo
    assert service.fade_samples([501], fade_in_ms=0, fade_out_ms=0.1) == 1
    assert service.require_sample(501).audio_data == b"\0\0"


def test_invalid_fades_and_missing_slot_leave_batch_unchanged():
    service = library()
    before = service.require_sample(501).audio_data
    for duration in [-1, 101, float("nan"), float("inf")]:
        with pytest.raises(ServiceError):
            service.fade_samples([501], fade_in_ms=duration, fade_out_ms=5)
    with pytest.raises(ServiceError):
        service.fade_samples([501, 999], fade_in_ms=5, fade_out_ms=5)
    assert service.require_sample(501).audio_data == before
    assert not service.can_undo


def test_usage_bank_last_part_muted_empty_and_remapping():
    service = library()
    pattern = PatternFile.new().to_bytes()
    service.patterns = PatternFile(pattern[:256] + bytes(65536) + pattern[256:] * 250)
    service.patterns.set_part(249, 15, slot=501, level=100, pan=0, mute=True, last_step=16)
    before = service.patterns.to_bytes()
    usage = service.sample_usage([501, 999])
    assert len(usage[501]) == 4000 and usage[999] == []
    assert usage[501][-1] == dict(pattern=250, name="Init", part=16, mute=True)
    assert service.patterns.to_bytes() == before and not service.can_undo
    service.move(501, 550)
    assert service.sample_usage([501])[501] == []
    assert len(service.sample_usage([550])[550]) == 4000
    service.undo()
    assert len(service.sample_usage([501])[501]) == 4000


def test_usage_missing_or_synth_patterns_is_not_reported_as_unused():
    service = library()
    with pytest.raises(ServiceError):
        service.sample_usage([501])
    raw = bytearray(PatternFile.new().to_bytes())
    raw[16:32] = b"electribe".ljust(16, b"\0")
    service.patterns = PatternFile(raw)
    with pytest.raises(ServiceError):
        service.sample_usage([501])


def test_fade_and_usage_dialogs(qtbot):
    service = library()
    service.patterns = PatternFile.new()
    window = MainWindow(library_service=service)
    qtbot.addWidget(window)
    window.table.view.selectRow(0)
    assert window.fade_action.isEnabled() and window.usage_action.isEnabled()
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
    window.fade_selected()
    assert service.can_undo
    window.table.view.selectRow(0)
    reports = []

    def read_report():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        dialog.accept()

    QTimer.singleShot(0, read_report)
    window.show_sample_usage()
    assert "Pattern 001 · Init · Part 16" in reports[0]
    assert "16 Part-Zuordnungen" in window._usage_warning([501])
    service.undo()
    service.patterns = None
