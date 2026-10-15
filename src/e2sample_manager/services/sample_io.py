"""Import and export of ordinary audio files and Electribe WAVE samples."""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from e2sample_manager.domain.models import Sample, SampleMetadata
from e2sample_manager.formats.riff import EsliCodec, RiffChunk, WaveFile, parse_chunks

from .errors import ServiceError


@dataclass(frozen=True, slots=True)
class AudioData:
    """Normalized signed 16-bit PCM audio."""

    frames: np.ndarray
    sample_rate: int
    cue_frame: int | None = None
    loop_start_frame: int | None = None
    loop_end_frame: int | None = None

    @property
    def channels(self) -> int:
        return int(self.frames.shape[1])


def _normalize_channels(frames: np.ndarray) -> np.ndarray:
    if frames.ndim == 1:
        return frames[:, None]
    if frames.ndim != 2 or frames.shape[1] == 0:
        raise ServiceError("Die Audiodatei enthält keine verwendbaren Kanäle.")
    if frames.shape[1] <= 2:
        return frames
    # Keep every input channel audible while producing a conventional stereo file.
    left = frames[:, 0::2].mean(axis=1)
    right = frames[:, 1::2].mean(axis=1)
    if frames[:, 1::2].shape[1] == 0:
        right = left
    return np.column_stack((left, right))


def _to_int16(frames: np.ndarray) -> np.ndarray:
    if frames.dtype == np.int16:
        return np.ascontiguousarray(frames)
    values = np.asarray(frames, dtype=np.float64)
    values = np.nan_to_num(values, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.ascontiguousarray(np.rint(np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2"))


def mix_to_mono(frames: np.ndarray, left: float = 0.5, right: float = 0.5) -> np.ndarray:
    """Mix normalized PCM to mono using independently controllable L/R gains."""

    data = np.asarray(frames)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] not in (1, 2):
        raise ServiceError("Mono-Mix erwartet ein- oder zweikanalige Audiodaten.")
    if not np.isfinite(left) or not np.isfinite(right):
        raise ServiceError("Die L/R-Mixwerte müssen endliche Zahlen sein.")
    if data.shape[1] == 1:
        return np.ascontiguousarray(data.astype("<i2", copy=False))
    mixed = data[:, 0].astype(np.float64) * left + data[:, 1].astype(np.float64) * right
    return np.ascontiguousarray(np.clip(np.rint(mixed), -32768, 32767).astype("<i2")[:, None])


def _read_wave_markers(path: Path) -> tuple[int | None, int | None, int | None]:
    """Read the first useful cue and smpl loop from a RIFF/WAVE file."""

    try:
        raw = path.read_bytes()
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return None, None, None
        chunks = parse_chunks(raw[12:], context="WAVE")
    except (OSError, ValueError):
        return None, None, None
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    cue: int | None = None
    cue_chunk = by_id.get(b"cue ")
    if cue_chunk is not None and len(cue_chunk.data) >= 28:
        count = struct.unpack_from("<I", cue_chunk.data)[0]
        if count and len(cue_chunk.data) >= 4 + 24:
            cue = struct.unpack_from("<I", cue_chunk.data, 4 + 20)[0]
    loop_start: int | None = None
    loop_end: int | None = None
    smpl = by_id.get(b"smpl")
    if smpl is not None and len(smpl.data) >= 60:
        count = struct.unpack_from("<I", smpl.data, 28)[0]
        if count:
            loop_start, loop_end = struct.unpack_from("<II", smpl.data, 44)
    return cue, loop_start, loop_end


def read_audio(
    path: str | Path, *, mono: bool = False, left: float = 0.5, right: float = 0.5
) -> AudioData:
    """Decode any libsndfile-supported file and normalize it to PCM16."""

    source = Path(path)
    try:
        frames, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    except Exception as exc:
        raise ServiceError(f"Audiodatei '{source}' kann nicht gelesen werden: {exc}") from exc
    normalized = _to_int16(_normalize_channels(frames))
    if normalized.shape[0] == 0:
        raise ServiceError(f"Audiodatei '{source}' enthält keine Samples.")
    if mono:
        normalized = mix_to_mono(normalized, left, right)
    markers = (
        _read_wave_markers(source)
        if source.suffix.lower() in {".wav", ".wave"}
        else (
            None,
            None,
            None,
        )
    )
    return AudioData(normalized, int(sample_rate), *markers)


def _marker_bytes(audio: AudioData) -> tuple[int, int, int]:
    block_align = audio.channels * 2
    final_byte = audio.frames.shape[0] * block_align - block_align
    start = min(max((audio.cue_frame or 0) * block_align, 0), final_byte)
    loop = min(max((audio.loop_start_frame or 0) * block_align, start), final_byte)
    if audio.loop_end_frame is not None:
        end_absolute = min(max(audio.loop_end_frame * block_align, start), final_byte)
        end = end_absolute - start
    else:
        end = final_byte - start
    return start, loop, end


def create_sample(
    audio: AudioData,
    *,
    slot: int,
    name: str = "",
    category: int = 17,
) -> Sample:
    """Create a complete Electribe-compatible WAVE sample."""

    if audio.channels not in (1, 2):
        raise ServiceError("Electribe-Samples müssen mono oder stereo sein.")
    clean_name = name.encode("ascii", "replace").decode("ascii")[:16]
    start, loop_start, end = _marker_bytes(audio)
    metadata = SampleMetadata(
        slot=slot,
        name=clean_name,
        category=category,
        start=start,
        loop_start=loop_start,
        end=end,
        one_shot=audio.loop_start_frame is None,
        sample_rate=audio.sample_rate,
        stereo=audio.channels == 2,
    )
    raw_audio = np.asarray(audio.frames, dtype="<i2").tobytes(order="C")
    block_align = audio.channels * 2
    fmt = struct.pack(
        "<HHIIHH",
        1,
        audio.channels,
        audio.sample_rate,
        audio.sample_rate * block_align,
        block_align,
        16,
    )
    initial = bytearray(EsliCodec.SIZE)
    initial[22:34] = b"\x00\x00\x00\x7f\x00\x01\x00\x00\x00\x00\x00\x00"
    initial[67:72] = b"\x01\xb0\x04\x00\x00"
    struct.pack_into("<H", initial, 34, max(0, round(63132 - math.log2(audio.sample_rate) * 3072)))
    struct.pack_into("<H", initial, 36, 65535)
    initial[64] = 1  # first audio channel enabled
    esli = EsliCodec.encode(bytes(initial), metadata, audio_size=len(raw_audio))
    wave = WaveFile(
        [
            RiffChunk(b"fmt ", fmt),
            RiffChunk(b"data", raw_audio),
            RiffChunk(b"korg", RiffChunk(b"esli", esli).to_bytes()),
        ]
    )
    return Sample(metadata=metadata, wave=wave)


def import_sample(
    path: str | Path,
    *,
    slot: int,
    name: str | None = None,
    mono: bool = False,
    left: float = 0.5,
    right: float = 0.5,
    category: int = 17,
) -> Sample:
    source = Path(path)
    audio = read_audio(source, mono=mono, left=left, right=right)
    return create_sample(audio, slot=slot, name=name or source.stem, category=category)


def _cue_chunk(frame: int) -> RiffChunk:
    payload = struct.pack("<III4sIII", 1, 1, 0, b"data", 0, 0, frame)
    return RiffChunk(b"cue ", payload)


def _smpl_chunk(sample_rate: int, start: int, end: int) -> RiffChunk:
    period = round(1_000_000_000 / sample_rate)
    header = struct.pack("<9I", 0, 0, period, 60, 0, 0, 0, 1, 0)
    loop = struct.pack("<6I", 1, 0, start, end, 0, 0)
    return RiffChunk(b"smpl", header + loop)


def standard_wave(sample: Sample, *, include_markers: bool = False) -> WaveFile:
    """Return a standard PCM WAVE without proprietary KORG metadata."""

    fmt = RiffChunk(b"fmt ", sample.wave.require_chunk(b"fmt ").data)
    data = RiffChunk(b"data", sample.audio_data)
    chunks = [fmt]
    channels, sample_rate, block_align = sample.wave.pcm_format
    metadata = sample.metadata
    if include_markers:
        cue_frame = metadata.start // block_align
        chunks.append(_cue_chunk(cue_frame))
        if not metadata.one_shot:
            loop_start = metadata.loop_start // block_align
            loop_end = min(
                (metadata.start + metadata.end) // block_align,
                len(data.data) // block_align - 1,
            )
            chunks.append(_smpl_chunk(sample_rate, loop_start, loop_end))
    chunks.append(data)
    return WaveFile(chunks)


def export_sample(
    sample: Sample,
    path: str | Path,
    *,
    include_markers: bool = False,
) -> Path:
    """Write one sample as a standard PCM WAVE."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wave = standard_wave(sample, include_markers=include_markers)
    # Standard waves deliberately have no korg chunk, so WaveFile.validate is not used.
    try:
        target.write_bytes(wave.to_bytes())
    except OSError as exc:
        raise ServiceError(f"WAVE-Datei '{target}' kann nicht geschrieben werden: {exc}") from exc
    return target


def export_samples(
    samples: Iterable[Sample],
    directory: str | Path,
    *,
    include_markers: bool = False,
) -> list[Path]:
    """Export samples with collision-safe slot-prefixed names."""

    folder = Path(directory)
    exported: list[Path] = []
    for sample in samples:
        name = "".join(
            char if char.isalnum() or char in " -_." else "_" for char in sample.metadata.name
        )
        target = folder / f"{sample.metadata.slot:04d}_{name or 'sample'}.wav"
        exported.append(export_sample(sample, target, include_markers=include_markers))
    return exported
