from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.services import AudioData, LibraryService, create_sample
from e2sample_manager.ui import MainWindow, SliceEditorDialog, WaveformEditorDialog


class SilentAudio:
    def __init__(self) -> None:
        self.played = False
        self.closed = False

    def play_full(self, _sample, **_kwargs) -> None:
        self.played = True

    def play_region(self, _sample, **_kwargs) -> None:
        self.played = True

    def stop(self) -> None:
        self.played = False

    def close(self) -> None:
        self.closed = True


def sample():
    frames = np.arange(512, dtype=np.int16).reshape(-1, 1)
    return create_sample(AudioData(frames, 44_100), slot=501, name="Test")


def test_main_window_smoke_and_editing(qtbot) -> None:
    service = LibraryService(E2SAllFile([sample()]))
    window = MainWindow(library_service=service, audio_service=SilentAudio())
    qtbot.addWidget(window)
    window.show()

    assert window.table.model.rowCount() == 1
    assert window.count_label.text() == "1 Sample"
    index = window.table.model.index(0, 1)
    assert window.table.model.setData(index, "Kick", Qt.EditRole)
    assert service.library.samples[0].metadata.name == "Kick"
    assert service.dirty
    window.undo()
    assert service.library.samples[0].metadata.name == "Test"


def test_waveform_and_slice_dialogs_construct_offscreen(qtbot) -> None:
    item = sample()
    service = LibraryService(E2SAllFile([item]))
    audio = SilentAudio()
    waveform = WaveformEditorDialog(service, audio, item)
    slices = SliceEditorDialog(service, audio, item)
    qtbot.addWidget(waveform)
    qtbot.addWidget(slices)

    waveform.show()
    slices.show()
    assert waveform.waveform.frame_count == 512
    assert slices.table.rowCount() == 64
    assert slices.table.columnCount() == 6


def test_application_entrypoint_starts_and_exits_offscreen() -> None:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["E2SAMPLE_MANAGER_SMOKE_TIMEOUT_MS"] = "50"

    completed = subprocess.run(
        [sys.executable, "-m", "e2sample_manager.app"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
