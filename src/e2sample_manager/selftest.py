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
