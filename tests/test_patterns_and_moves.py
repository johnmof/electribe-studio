from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from e2sample_manager.domain.profiles import HACKTRIBE_PROFILE
from e2sample_manager.formats.e2s_all import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.formats.riff import EsliCodec, FormatError
from e2sample_manager.services import AudioData, LibraryService, ServiceError, create_sample


def fixture_pattern(bank=False):
    """Independent binary fixture with sentinel data in unknown fields."""
    header = b"KORG".ljust(16, b"\0") + b"e2sampler".ljust(16, b"\0")
    header += b"\x01\0\0\0" + b"\xff" * (256 - 36)
    block = bytearray(b"\x5a" * 0x4000)
    block[:4] = b"PTST"
    block[0x3BFC:0x3C00] = b"PTED"
    block[16:34] = b"Fixture".ljust(18, b"\0")
    struct.pack_into("<H", block, 34, 1234)
    block[36:38] = bytes((251, 3))  # swing -5, four bars
    for part in range(16):
        b = 0x800 + part * 0x330
        block[b : b + 0x30] = bytes(0x30)
        struct.pack_into("<H", block, b + 8, 500 + part)
        for step in range(64):
            s = b + 0x30 + step * 12
            block[s : s + 8] = bytes((0, 72, 100, 1, 61, 0, 0, 0))
    return header + (b"\xff" * 0x10000 if bank else b"") + bytes(block) * (250 if bank else 1)


def service_for(slots, stereo=False):
    audio = AudioData(np.arange(128, dtype=np.int16).reshape(-1, 2 if stereo else 1), 44100)
    samples = [create_sample(audio, slot=s, name=f"Sample {s}") for s in slots]
    service = LibraryService(E2SAllFile(samples))
    service.patterns = PatternFile(fixture_pattern())
    service._saved_patterns = service.patterns.to_bytes()
    return service


@pytest.mark.parametrize("bank", [False, True])
def test_pattern_roundtrip_preserves_every_unedited_byte(bank):
    raw = fixture_pattern(bank)
    patterns = PatternFile(raw)
    assert patterns.count == (250 if bank else 1)
    assert patterns.to_bytes() == raw
    assert patterns.info(0) == dict(name="Fixture", tempo=123.4, swing=-5, length=4)
    assert patterns.oscillator(patterns.count - 1, 15) == 516
    assert patterns.extract(patterns.count - 1).to_bytes() == fixture_pattern(False)


def test_edit_last_step_uses_documented_offsets_and_preserves_reserved_bytes():
    original = fixture_pattern()
    patterns = PatternFile(original)
    patterns.set_step(
        0, 15, 63, on=True, gate=127, velocity=110, trigger=False, notes=[0, 60, 127, -1]
    )
    start = 0x100 + 0x800 + 15 * 0x330 + 0x30 + 63 * 12
    result = patterns.to_bytes()
    assert result[start : start + 12] == bytes((1, 127, 110, 0, 1, 61, 128, 0, 90, 90, 90, 90))
    assert result[:start] == original[:start]
    assert result[start + 12 :] == original[start + 12 :]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw[:-1],
        lambda raw: b"NOPE" + raw[4:],
        lambda raw: raw[:0x100] + b"BAD!" + raw[0x104:],
    ],
)
def test_malformed_patterns_rejected(mutate):
    with pytest.raises(FormatError):
        PatternFile(mutate(fixture_pattern()))


def test_pattern_metadata_and_part_parameters_use_exact_bytes():
    pattern = PatternFile(fixture_pattern())
    pattern.set_info(0, name="Berlin", tempo=146.7, swing=-23, length=2)
    pattern.set_part(0, 1, slot=999, level=110, pan=-30, mute=True, last_step=7)
    assert pattern.data[0x122:0x126] == struct.pack("<HbB", 1467, -23, 1)
    assert pattern.part_info(0, 1) == dict(slot=999, level=110, pan=-30, mute=True, last_step=7)


def test_overlapping_range_moves_are_simultaneous_and_undo_patterns_together():
    service = service_for([501, 502, 503])
    original = service.patterns.to_bytes()
    assert service.move_range(501, 503, 502) == {501: 502, 502: 503, 503: 504}
    assert service.require_sample(502).metadata.name == "Sample 501"
    assert [service.patterns.oscillator(0, p) for p in range(3)] == [502, 503, 504]
    assert service.pattern_dirty
    assert service.undo()
    assert service.patterns.to_bytes() == original
    assert service.require_sample(501).metadata.name == "Sample 501"
    assert not service.dirty
    assert service.redo()
    assert service.patterns.oscillator(0, 0) == 502


def test_range_move_preserves_gaps_and_target_occupants():
    service = service_for([501, 503, 700])
    assert service.move_range(501, 503, 350) == {501: 350, 503: 352}
    assert service.library.by_slot(351) is None
    before = service.library.to_bytes()
    patterns_before = service.patterns.to_bytes()
    with pytest.raises(ServiceError, match="bereits belegt"):
        service.move_range(350, 352, 698)
    assert service.library.to_bytes() == before
    assert service.patterns.to_bytes() == patterns_before


def test_user_example_invalid_reserved_slots_is_blocked_with_reason():
    service = service_for(range(501, 601))
    with pytest.raises(ServiceError, match="reserviert"):
        service.move_range(500, 600, 350)
    service.set_profile(HACKTRIBE_PROFILE)
    with pytest.raises(ServiceError, match="501–999"):
        service.move_range(501, 510, 350)


def test_hacktribe_conversion_keeps_pattern_sample_identity():
    service = service_for([19, 350, 501])
    for part, slot in enumerate((19, 350, 501)):
        service.patterns.set_oscillator(0, part, slot)
    service.arrange_hacktribe()
    assert [service.patterns.oscillator(0, p) for p in range(3)] == [501, 502, 503]
    service.library.validate(HACKTRIBE_PROFILE)


def test_bank_remapping_never_cascades_and_does_not_modify_motion_or_headers():
    raw = fixture_pattern(True)
    pattern = PatternFile(raw)
    assert pattern.remap({501: 502, 502: 503, 516: 999}) == 750
    allowed = {
        0x10100 + i * 0x4000 + 0x800 + p * 0x330 + 8 + j
        for i in range(250)
        for p in (0, 1, 15)
        for j in (0, 1)
    }
    assert all(
        i in allowed for i, (a, b) in enumerate(zip(raw, pattern.data, strict=True)) if a != b
    )


def test_save_both_files_and_read_back_on_windows(tmp_path):
    service = service_for([501, 502])
    service.move_range(501, 502, 350)
    sample_path = tmp_path / "e2sSample.all"
    pattern_path = tmp_path / "Beat.e2spat"
    service.save_as(sample_path)
    assert service.pattern_dirty and not service.sample_dirty
    service.save_patterns(pattern_path)
    assert not service.dirty
    assert E2SAllFile.load(sample_path).by_slot(350).metadata.name == "Sample 501"
    assert PatternFile.load(pattern_path).oscillator(0, 0) == 350
    before = pattern_path.read_bytes()
    service.edit_pattern(lambda p: p.set_oscillator(0, 0, 351))
    service.save_patterns(pattern_path)
    assert pattern_path.with_suffix(".e2spat.bak").read_bytes() == before
    assert service.undo()
    assert service.pattern_dirty


def test_failed_pattern_save_does_not_lose_previous_file(tmp_path, monkeypatch):
    import e2sample_manager.formats.patterns as module

    path = tmp_path / "pattern.e2spat"
    original = fixture_pattern()
    path.write_bytes(original)

    def fail(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        PatternFile.new().save(path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_mono_conversion_preserves_slice_frame_positions_and_loop_frames():
    service = service_for([501], stereo=True)
    sample = service.require_sample(501)
    sample.metadata.start, sample.metadata.loop_start, sample.metadata.end = 8, 16, 32
    sample.metadata.slices[0].start = 3
    sample.metadata.slices[0].length = 6
    old_size = sample.memory_size
    service.convert_mono([501])
    sample = service.require_sample(501)
    assert sample.memory_size == old_size // 2
    assert not sample.metadata.stereo
    assert (sample.metadata.start, sample.metadata.loop_start, sample.metadata.end) == (4, 8, 16)
    assert sample.metadata.slices[0].start == 3
    service.library.validate(HACKTRIBE_PROFILE)
    assert service.undo()
    assert service.require_sample(501).metadata.stereo


def test_import_initializes_firmware_playback_fields_and_relative_loop_offset():
    audio = AudioData(np.zeros((64, 1), dtype=np.int16), 44100, 4, 12, 40)
    sample = create_sample(audio, slot=501)
    raw = sample.wave.esli_chunk().data
    assert struct.unpack_from("<H", raw, 34)[0] == round(63132 - math.log2(44100) * 3072)
    assert struct.unpack_from("<H", raw, 36)[0] == 65535
    assert raw[64] == 1
    assert raw[22:34] == bytes.fromhex("0000007f0001000000000000")
    assert raw[67:72] == bytes.fromhex("01b0040000")
    assert struct.unpack_from("<III", raw, 40) == (8, 16, 72)
    assert EsliCodec.decode(raw).loop_start == 24


def test_pattern_ui_edits_final_bar_and_undo(qtbot):
    from e2sample_manager.ui.pattern_editor import PatternEditor

    service = service_for([501])
    editor = PatternEditor(service)
    qtbot.addWidget(editor)
    editor.bar.setCurrentIndex(3)
    editor.grid.setCurrentCell(15, 15)
    editor.notes[0].setValue(127)
    editor.on.setChecked(True)
    editor.apply_step()
    assert service.patterns.step(0, 15, 63)["notes"][0] == 127
    assert service.patterns.step(0, 15, 63)["on"]
    service.undo()
    editor.refresh()
    assert editor.notes[0].value() == 60


def test_public_real_pattern_bank_roundtrips_when_downloaded():
    from pathlib import Path

    source = Path(".reference/init.e2sallpat")
    if not source.exists():
        pytest.skip("Optional upstream integration fixture not downloaded")
    raw = source.read_bytes()
    pattern = PatternFile(raw)
    assert pattern.to_bytes() == raw
    assert pattern.count == 250
    assert pattern.info(249)["tempo"] == 120
    pattern.set_oscillator(249, 15, 999)
    assert pattern.oscillator(249, 15) == 999
