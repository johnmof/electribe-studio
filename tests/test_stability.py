import os
import struct
import subprocess
import sys

import numpy as np
import pytest

from e2sample_manager.formats import E2SAllFile, FormatError
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample


def test_qt_bank_switch_and_editor_regression_in_separate_process():
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-m", "e2sample_manager.app", "--ui-self-test"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: 36 bank changes" in result.stdout


def test_samplerate_changes_rejected_before_modifying_the_document():
    sample = create_sample(AudioData(np.ones((16, 1), dtype=np.int16), 44100), slot=501)
    service = LibraryService(E2SAllFile([sample]))
    before = service.library.to_bytes()
    for rate in (0, 48000, 2**32):
        with pytest.raises(ServiceError, match="schreibgeschützt"):
            service.update_metadata(501, sample_rate=rate)
    assert service.library.to_bytes() == before
    assert not service.dirty and not service.can_undo


def test_invalid_pcm_rejected_when_reading_all_file():
    sample = create_sample(AudioData(np.ones((16, 1), dtype=np.int16), 44100), slot=501)
    raw = bytearray(E2SAllFile([sample]).to_bytes())
    fmt = raw.index(b"fmt ", 4096)
    struct.pack_into("<H", raw, fmt + 8 + 14, 8)
    with pytest.raises(FormatError, match="16-bit PCM"):
        E2SAllFile.from_bytes(raw)
