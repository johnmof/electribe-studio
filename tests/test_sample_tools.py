import copy

import numpy as np
import pytest

from e2sample_manager.formats import E2SAllFile
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample


def sample(slot, values, rate=44100):
    return create_sample(AudioData(np.array(values, dtype=np.int16), rate), slot=slot)


def test_normalize_linked_stereo_preserves_markers_and_undo():
    item = sample(501, [[-32768, 16384], [0, 8192]])
    meta = copy.deepcopy(item.metadata)
    original = item.audio_data
    service = LibraryService(E2SAllFile([item]))
    assert service.normalize_samples([501, 501]) == 1
    result = np.frombuffer(item.audio_data, dtype="<i2").astype(int)
    target = round(32767 * 10 ** (-1 / 20))
    assert max(abs(result)) == target
    assert result[1] == pytest.approx(-result[0] / 2, abs=1)
    assert item.metadata == meta
    assert service.undo()
    assert service.require_sample(501).audio_data == original
    assert not service.can_undo
    assert service.redo()
    assert service.require_sample(501).audio_data != original


def test_normalize_silence_is_noop_and_missing_slot_is_atomic():
    item = sample(501, [[0], [0]])
    service = LibraryService(E2SAllFile([item]))
    assert service.normalize_samples([501]) == 0
    assert not service.dirty and not service.can_undo
    item.audio_data = np.array([100, -100], dtype="<i2").tobytes()
    original = item.audio_data
    with pytest.raises(ServiceError):
        service.normalize_samples([501, 999])
    assert item.audio_data == original
    assert not service.can_undo


def test_normalize_batch_roundtrip(tmp_path):
    service = LibraryService(
        E2SAllFile([sample(501, [[100], [-100]]), sample(502, [[200], [-200]])])
    )
    assert service.normalize_samples([501, 502]) == 2
    service.save_as(tmp_path / "normalized.all")
    loaded = E2SAllFile.load(tmp_path / "normalized.all")
    assert loaded.by_slot(501).audio_data == loaded.by_slot(502).audio_data
    assert service.normalize_samples([501, 502]) == 0


def test_duplicates_compare_format_not_names_or_markers():
    first = sample(501, [[100], [200]])
    twin = copy.deepcopy(first)
    twin.metadata.slot = 505
    twin.metadata.name = "Another name"
    twin.metadata.one_shot = False
    different_rate = sample(503, [[100], [200]], rate=48000)
    stereo = sample(504, [[100, 200]])
    different_audio = sample(502, [[100], [201]])
    service = LibraryService(E2SAllFile([twin, first, different_rate, stereo, different_audio]))
    assert service.find_duplicate_audio() == [[501, 505]]
    assert not service.dirty and not service.can_undo
