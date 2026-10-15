from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from e2sample_manager.domain.profiles import HACKTRIBE_PROFILE
from e2sample_manager.formats.e2s_all import E2SAllFile
from e2sample_manager.formats.riff import parse_chunks
from e2sample_manager.services import (
    AudioData,
    AudioService,
    LibraryService,
    ServiceError,
    create_sample,
    import_sample,
    mix_to_mono,
    read_audio,
)


def audio(channels: int = 1, frames: int = 16) -> AudioData:
    values = np.arange(frames * channels, dtype=np.int16).reshape(frames, channels)
    return AudioData(values, 44_100)


def test_import_normalizes_audio_and_builds_electribe_wave(tmp_path: Path) -> None:
    path = tmp_path / "stereo.aiff"
    sf.write(path, np.array([[0.5, -0.5], [1.0, -1.0]], dtype=np.float32), 48_000)

    sample = import_sample(path, slot=501)

    assert sample.metadata.name == "stereo"
    assert sample.metadata.stereo
    assert sample.metadata.sample_rate == 48_000
    assert sample.metadata.end == len(sample.audio_data) - 4
    assert sample.wave.get_chunk(b"korg") is not None
    sample.wave.validate()


def test_mono_mix_uses_independent_left_and_right_levels() -> None:
    stereo = np.array([[10_000, -10_000], [20_000, 10_000]], dtype=np.int16)
    assert mix_to_mono(stereo, 1.0, 0.0)[:, 0].tolist() == [10_000, 20_000]
    assert mix_to_mono(stereo, 0.0, 1.0)[:, 0].tolist() == [-10_000, 10_000]


def test_stereo_import_can_mix_to_mono(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    sf.write(
        path,
        np.array([[10_000, -10_000], [20_000, 10_000]], dtype=np.int16),
        44_100,
        subtype="PCM_16",
    )

    decoded = read_audio(path, mono=True, left=1.0, right=0.0)
    sample = import_sample(path, slot=501, mono=True, left=1.0, right=0.0)

    assert decoded.channels == 1
    assert decoded.frames[:, 0].tolist() == pytest.approx([10_000, 20_000], abs=1)
    assert sample.metadata.stereo is False
    assert sample.wave.pcm_format == (1, 44_100, 2)


def test_late_cue_and_loop_markers_remain_absolute() -> None:
    marked = AudioData(
        np.arange(16, dtype=np.int16).reshape(-1, 1),
        44_100,
        cue_frame=10,
        loop_start_frame=12,
        loop_end_frame=15,
    )

    sample = create_sample(marked, slot=501)

    assert sample.metadata.start == 20
    assert sample.metadata.loop_start == 24
    assert sample.metadata.end == 10
    E2SAllFile([sample]).validate(HACKTRIBE_PROFILE)


def test_export_standard_wave_and_reimport_markers(tmp_path: Path) -> None:
    sample = create_sample(audio(), slot=501, name="Loop")
    sample.metadata.one_shot = False
    sample.metadata.start = 2
    sample.metadata.loop_start = 6
    sample.metadata.end = len(sample.audio_data) - 4
    service = LibraryService(E2SAllFile([sample]))
    target = tmp_path / "export.wav"

    service.export(501, target, include_markers=True)

    raw = target.read_bytes()
    chunks = {item.chunk_id: item for item in parse_chunks(raw[12:])}
    assert {b"fmt ", b"data", b"cue ", b"smpl"} <= chunks.keys()
    assert b"korg" not in chunks
    imported = import_sample(target, slot=502)
    assert imported.metadata.start == 2
    assert imported.metadata.loop_start == 6
    assert imported.metadata.one_shot is False


def test_exported_wave_is_pcm16_and_preserves_audio(tmp_path: Path) -> None:
    sample = create_sample(audio(channels=2), slot=501)
    target = tmp_path / "standard.wav"

    LibraryService(E2SAllFile([sample])).export(501, target)

    frames, sample_rate = sf.read(target, dtype="int16", always_2d=True)
    assert sample_rate == 44_100
    assert frames.shape == (16, 2)
    assert frames.tobytes() == sample.audio_data


def test_library_edits_and_undo_redo_use_deep_snapshots() -> None:
    first = create_sample(audio(), slot=501, name="First")
    second = create_sample(audio(), slot=503, name="Second")
    service = LibraryService(E2SAllFile([first, second]))

    assert service.fill_gaps() == [501, 502]
    duplicate = service.duplicate(501, 504)
    duplicate.metadata.name = "Changed outside history"
    service.swap(501, 502)
    service.delete(504)
    assert [item.metadata.slot for item in service.library.samples] == [501, 502]

    assert service.undo()
    assert service.library.by_slot(504) is not None
    assert service.undo()
    assert service.library.by_slot(501).metadata.name == "First"
    assert service.redo()
    assert service.redo()
    assert service.library.by_slot(504) is None


def test_undo_redo_tracks_saved_state_and_failed_edits_are_atomic(tmp_path: Path) -> None:
    service = LibraryService(E2SAllFile([create_sample(audio(), slot=501, name="Original")]))
    service.save_as(tmp_path / "e2sSample.all")
    assert not service.dirty

    service.update_metadata(501, name="Edited")
    assert service.dirty
    with pytest.raises(ServiceError, match="bereits belegt"):
        service.duplicate(501, 501)
    assert service.library.by_slot(501).metadata.name == "Edited"

    assert service.undo()
    assert service.library.by_slot(501).metadata.name == "Original"
    assert not service.dirty
    assert service.redo()
    assert service.library.by_slot(501).metadata.name == "Edited"
    assert service.dirty


def test_multiple_import_replace_move_and_hacktribe_arrange(tmp_path: Path) -> None:
    paths = []
    for index in range(2):
        path = tmp_path / f"{index}.wav"
        sf.write(path, np.arange(8, dtype=np.int16), 44_100, subtype="PCM_16")
        paths.append(path)
    service = LibraryService(profile=HACKTRIBE_PROFILE)

    imported = service.import_samples(paths, first_slot=501)
    assert [item.metadata.slot for item in imported] == [501, 502]
    replacement = service.replace(501, paths[1])
    assert replacement.metadata.slot == 501
    service.move(502, 510)
    assert service.library.by_slot(510) is not None
    assert service.arrange_hacktribe() == [501, 502]


def test_save_validates_before_atomic_write(tmp_path: Path, monkeypatch) -> None:
    service = LibraryService(E2SAllFile([create_sample(audio(), slot=501)]))
    calls: list[str] = []

    def validate(self, profile) -> None:
        calls.append(f"validate:{profile.name}")

    def save_atomic(self, path, *, backup=True) -> None:
        calls.append(f"save:{Path(path).name}:{backup}")

    monkeypatch.setattr(E2SAllFile, "validate", validate)
    monkeypatch.setattr(E2SAllFile, "save_atomic", save_atomic)

    service.save_as(tmp_path / "e2sSample.all", backup=False)
    assert calls == ["validate:KORG electribe sampler", "save:e2sSample.all:False"]


class FakeStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.active = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True


def test_audio_service_is_nonblocking_and_closes_stream(monkeypatch) -> None:
    import sounddevice

    streams: list[FakeStream] = []

    def output_stream(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(sounddevice, "OutputStream", output_stream)
    sample = create_sample(audio(channels=2), slot=501)
    sample.metadata.tune = 12
    service = AudioService()

    service.play_full(sample)

    assert service.is_playing
    assert streams[0].kwargs["samplerate"] == 88_200
    assert streams[0].kwargs["channels"] == 2
    service.stop()
    assert streams[0].closed
    assert not service.is_playing


def test_missing_audio_device_becomes_service_error(monkeypatch) -> None:
    import sounddevice

    def unavailable(**_kwargs):
        raise RuntimeError("no default output device")

    monkeypatch.setattr(sounddevice, "OutputStream", unavailable)
    with pytest.raises(ServiceError, match="Ausgabegerät"):
        AudioService().play_full(create_sample(audio(), slot=501))


def test_validation_rejects_inconsistent_metadata() -> None:
    sample = create_sample(audio(), slot=501)
    sample.metadata.end = len(sample.audio_data)
    with pytest.raises(Exception, match="end lies beyond"):
        E2SAllFile([sample]).validate(HACKTRIBE_PROFILE)
