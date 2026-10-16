"""Render reproducible UI screenshots using generated audio only."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from e2sample_manager.domain.profiles import HACKTRIBE_PROFILE
from e2sample_manager.formats.e2s_all import E2SAllFile
from e2sample_manager.services import AudioData, LibraryService, create_sample
from e2sample_manager.ui.main_window import MainWindow
from e2sample_manager.ui.theme import apply_theme
from e2sample_manager.ui.waveform_editor import WaveformEditorDialog

app = QApplication([])
QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
QFontDatabase.addApplicationFont("C:/Windows/Fonts/seguisb.ttf")
apply_theme(app)
samples = []
names = [
    "Deep Kick",
    "Dusty Snare",
    "Closed Hat",
    "Open Hat",
    "Analog Bass",
    "Warm Chord",
    "Vinyl Clap",
    "Rim Shot",
    "Shaker",
    "Perc Loop",
    "Air Pad",
    "Tape Texture",
]
for i, name in enumerate(names):
    time = np.arange(44100 // 2) / 44100
    wave = np.sin(2 * np.pi * (50 + i * 43) * time) * np.exp(-time * (3 + i)) * 25000
    frames = wave.astype(np.int16)[:, None]
    if i in (5, 9, 10, 11):
        frames = np.column_stack((frames[:, 0], np.roll(frames[:, 0], 90)))
    samples.append(create_sample(AudioData(frames, 44100), slot=501 + i, name=name))
service = LibraryService(E2SAllFile(samples), profile=HACKTRIBE_PROFILE)
service.new_pattern()
service.patterns.set_info(0, name="Night Session", tempo=126.0, swing=8, length=4)
for p in range(12):
    service.patterns.set_oscillator(0, p, 501 + p)
    for s in range(64):
        on = (s % (4 if p == 0 else 8 if p == 1 else 2 if p == 2 else 16)) == (
            0 if p % 2 == 0 else 4
        )
        service.patterns.set_step(
            0, p, s, on=on, gate=72, velocity=100, trigger=on, notes=[60 if on else -1, -1, -1, -1]
        )
window = MainWindow(library_service=service)
window.resize(1320, 900)
window.show()
window.table.view.selectRow(0)
app.processEvents()
output = Path("docs/images")
output.mkdir(parents=True, exist_ok=True)
window.grab().save(str(output / "samples.png"))
window.resize(1250, 850)
window.tabs.setCurrentIndex(1)
app.processEvents()
window.grab().save(str(output / "patterns.png"))
dialog = WaveformEditorDialog(service, window.audio, service.require_sample(501), window)
dialog.show()
app.processEvents()
dialog.grab().save(str(output / "waveform.png"))
window.audio.close()
print(f"Screenshots saved to {output.resolve()}")
