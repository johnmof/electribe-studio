from __future__ import annotations

import struct
from pathlib import Path

import pytest

import e2sample_manager.formats.e2s_all as e2s_all_module
from e2sample_manager.domain.models import Sample, SampleMetadata, Slice
from e2sample_manager.domain.profiles import (
    HACKTRIBE_PROFILE,
    KORG_PROFILE,
    DeviceProfile,
)
from e2sample_manager.formats.e2s_all import (
    HEADER_SIZE,
    MAGIC,
    POINTER_COUNT,
    E2SAllFile,
)
from e2sample_manager.formats.riff import (
    EsliCodec,
    FormatError,
    RiffChunk,
    WaveFile,
    parse_chunks,
)


def make_sample(
    slot: int = 501,
    *,
    name: str = "Kick",
    unknown: bytes = b"keep me",
) -> Sample:
    audio = struct.pack("<8h", *range(8))
    metadata = SampleMetadata(
        slot=slot,
        name=name,
        category=2,
        start=0,
        loop_start=4,
        end=14,
        one_shot=False,
        plus_12db=True,
        sample_rate=44_100,
        tune=-3,
        stereo=False,
        slices=[Slice(start=index, length=index + 1) for index in range(64)],
        steps=[(index % 5) - 2 for index in range(64)],
    )
    esli = EsliCodec.encode(bytes(EsliCodec.SIZE), metadata, audio_size=len(audio))
    nested = (
        RiffChunk(b"junk", b"nested?", b"\x7f").to_bytes() + RiffChunk(b"esli", esli).to_bytes()
    )
    fmt = struct.pack("<HHIIHH", 1, 1, 44_100, 88_200, 2, 16)
    wave = WaveFile(
        [
            RiffChunk(b"fmt ", fmt),
            RiffChunk(b"Xtra", unknown, b"\xa5"),
            RiffChunk(b"data", audio),
            RiffChunk(b"korg", nested),
        ]
    )
    return Sample(metadata=metadata, wave=wave)


def make_all(*samples: Sample) -> E2SAllFile:
    return E2SAllFile(samples=list(samples))


def test_riff_and_all_round_trip_rebuilds_pointer_table() -> None:
    library = make_all(make_sample(503), make_sample(501, name="Snare"))

    encoded = library.to_bytes()

    assert encoded[:16] == MAGIC
    assert len(encoded[:HEADER_SIZE]) == HEADER_SIZE
    pointers = struct.unpack_from(f"<{POINTER_COUNT}I", encoded, len(MAGIC))
    assert pointers[500] == HEADER_SIZE
    assert pointers[502] > pointers[500]
    assert sum(pointer != 0 for pointer in pointers) == 2
    decoded = E2SAllFile.from_bytes(encoded)
    assert [sample.metadata.slot for sample in decoded.samples] == [501, 503]
    assert decoded.to_bytes() == encoded


def test_esli_metadata_and_all_64_slices_and_steps_round_trip() -> None:
    encoded = make_all(make_sample()).to_bytes()

    metadata = E2SAllFile.from_bytes(encoded).samples[0].metadata

    assert metadata.name == "Kick"
    assert metadata.category == 2
    assert (metadata.start, metadata.loop_start, metadata.end) == (0, 4, 14)
    assert metadata.one_shot is False
    assert metadata.plus_12db is True
    assert metadata.sample_rate == 44_100
    assert metadata.tune == -3
    assert metadata.stereo is False
    assert len(metadata.slices) == len(metadata.steps) == 64
    assert metadata.slices[63] == Slice(start=63, length=64)
    assert metadata.steps[:5] == [-2, -1, 0, 1, 2]


def test_unknown_wave_and_nested_korg_chunks_are_preserved() -> None:
    original = make_all(make_sample(unknown=b"odd")).to_bytes()
    library = E2SAllFile.from_bytes(original)
    extra = library.samples[0].wave.require_chunk(b"Xtra")
    assert extra.data == b"odd"
    assert extra.pad == b"\xa5"

    library.samples[0].metadata.name = "Edited"
    rewritten = library.to_bytes()
    wave = E2SAllFile.from_bytes(rewritten).samples[0].wave

    assert wave.require_chunk(b"Xtra").to_bytes() == extra.to_bytes()
    korg_children = {
        chunk.chunk_id: chunk for chunk in parse_chunks(wave.require_chunk(b"korg").data)
    }
    assert korg_children[b"junk"].data == b"nested?"
    assert korg_children[b"junk"].pad == b"\x7f"


def test_profiles_arrangement_slots_duplicates_and_memory_limit() -> None:
    library = make_all(make_sample(19), make_sample(501))
    library.validate(KORG_PROFILE)
    with pytest.raises(FormatError, match="not valid"):
        library.validate(HACKTRIBE_PROFILE)
    assert HACKTRIBE_PROFILE.is_user_slot(501)
    assert not HACKTRIBE_PROFILE.is_user_slot(500)

    library.arrange_from_slot(501)
    library.validate(HACKTRIBE_PROFILE)
    assert [sample.metadata.slot for sample in library.samples] == [501, 502]

    library.samples[1].metadata.slot = 501
    with pytest.raises(FormatError, match="duplicate"):
        library.validate(KORG_PROFILE)

    small = DeviceProfile("tiny", (range(1, 1021),), memory_limit=1)
    library.samples[1].metadata.slot = 502
    with pytest.raises(FormatError, match="exceeding"):
        library.validate(small)


def test_arrangement_updates_korg_import_numbers_for_hacktribe() -> None:
    library = make_all(make_sample(19), make_sample(421))

    library.arrange_from_slot(501)
    decoded = E2SAllFile.from_bytes(library.to_bytes())

    first_esli = decoded.samples[0].wave.esli_chunk().data
    second_esli = decoded.samples[1].wave.esli_chunk().data
    assert [sample.metadata.slot for sample in decoded.samples] == [501, 502]
    assert struct.unpack_from("<H", first_esli, 20)[0] == 550
    assert struct.unpack_from("<H", second_esli, 20)[0] == 551


def test_korg_and_hacktribe_profile_boundaries() -> None:
    assert KORG_PROFILE.accepts_slot(19)
    assert KORG_PROFILE.accepts_slot(421)
    assert not KORG_PROFILE.accepts_slot(18)
    assert not KORG_PROFILE.accepts_slot(422)
    assert not KORG_PROFILE.accepts_slot(500)
    assert KORG_PROFILE.accepts_slot(501)
    assert KORG_PROFILE.accepts_slot(999)
    assert not KORG_PROFILE.accepts_slot(1000)
    assert not HACKTRIBE_PROFILE.accepts_slot(1)
    assert not HACKTRIBE_PROFILE.accepts_slot(500)
    assert HACKTRIBE_PROFILE.accepts_slot(501)
    assert HACKTRIBE_PROFILE.accepts_slot(999)
    assert not HACKTRIBE_PROFILE.accepts_slot(1020)
    assert not HACKTRIBE_PROFILE.accepts_slot(0)
    assert not HACKTRIBE_PROFILE.accepts_slot(1021)


def test_rewrite_recalculates_pointers_after_payload_size_change() -> None:
    library = make_all(make_sample(501), make_sample(502))
    original = library.to_bytes()
    original_pointers = struct.unpack_from(f"<{POINTER_COUNT}I", original, len(MAGIC))

    library.samples[0].wave.require_chunk(b"Xtra").data += b"larger"
    rewritten = library.to_bytes()
    rewritten_pointers = struct.unpack_from(f"<{POINTER_COUNT}I", rewritten, len(MAGIC))

    assert rewritten_pointers[500] == HEADER_SIZE
    assert rewritten_pointers[501] > original_pointers[501]
    assert E2SAllFile.from_bytes(rewritten).to_bytes() == rewritten


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: b"not sample all!" + data[15:], "magic"),
        (
            lambda data: data[:16] + struct.pack("<I", 1) + data[20:],
            "inside the header",
        ),
        (
            lambda data: data[:4100] + struct.pack("<I", 0x7FFFFFFF) + data[4104:],
            "extends beyond",
        ),
    ],
)
def test_corrupt_all_files_raise_clear_format_errors(mutate, message: str) -> None:
    valid = make_all(make_sample()).to_bytes()
    with pytest.raises(FormatError, match=message):
        E2SAllFile.from_bytes(mutate(valid))


def test_bad_riff_and_esli_lengths_are_rejected() -> None:
    wave = make_sample().wave.to_bytes()
    bad_riff = wave[:4] + struct.pack("<I", len(wave) + 100) + wave[8:]
    with pytest.raises(FormatError, match="length mismatch"):
        WaveFile.from_bytes(bad_riff)

    sample = make_sample()
    korg = sample.wave.require_chunk(b"korg")
    korg.data = RiffChunk(b"esli", bytes(100)).to_bytes()
    with pytest.raises(FormatError, match="1172"):
        sample.wave.validate()


def test_duplicate_and_non_riff_pointers_are_rejected() -> None:
    valid = bytearray(make_all(make_sample(501), make_sample(502)).to_bytes())
    first_pointer = struct.unpack_from("<I", valid, len(MAGIC) + 500 * 4)[0]
    struct.pack_into("<I", valid, len(MAGIC) + 501 * 4, first_pointer)
    with pytest.raises(FormatError, match="duplicate RIFF pointer"):
        E2SAllFile.from_bytes(bytes(valid))

    valid = bytearray(make_all(make_sample()).to_bytes())
    struct.pack_into("<I", valid, len(MAGIC) + 500 * 4, HEADER_SIZE + 1)
    with pytest.raises(FormatError, match="does not reference a RIFF"):
        E2SAllFile.from_bytes(bytes(valid))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"ABCD\x04\0\0\0xy", "beyond its container"),
        (b"ABCD\x01\0\0\0x", "alignment byte"),
        (b"x", "trailing byte"),
    ],
)
def test_corrupt_riff_chunk_streams_are_rejected(payload: bytes, message: str) -> None:
    with pytest.raises(FormatError, match=message):
        parse_chunks(payload)


def test_loop_metadata_uses_absolute_byte_positions() -> None:
    sample = make_sample()
    sample.metadata.start = 4
    sample.metadata.loop_start = 10
    sample.metadata.end = 10
    make_all(sample).validate(KORG_PROFILE)

    sample.metadata.loop_start = 2
    with pytest.raises(FormatError, match="precedes start"):
        make_all(sample).validate(KORG_PROFILE)


def test_atomic_save_keeps_previous_file_as_backup(tmp_path: Path) -> None:
    path = tmp_path / "e2sSample.all"
    path.write_bytes(b"old library")
    library = make_all(make_sample())

    library.save_atomic(path, backup=True)

    assert path.read_bytes() == library.to_bytes()
    assert path.with_name(path.name + ".bak").read_bytes() == b"old library"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_atomic_replace_keeps_target_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "e2sSample.all"
    path.write_bytes(b"old library")
    real_replace = e2s_all_module.os.replace

    def fail_target_replace(source, destination) -> None:
        if Path(destination) == path:
            raise OSError("simulated replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(e2s_all_module.os, "replace", fail_target_replace)
    with pytest.raises(FormatError, match="atomically save"):
        make_all(make_sample()).save_atomic(path)

    assert path.read_bytes() == b"old library"
    assert path.with_name(path.name + ".bak").read_bytes() == b"old library"
    assert not list(tmp_path.glob("*.tmp"))
