"""Lossless RIFF/WAVE and KORG ``esli`` primitives."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from e2sample_manager.domain.models import SampleMetadata, Slice


class FormatError(ValueError):
    """Raised when an input file violates a binary-format invariant."""


def _checked_chunk_id(chunk_id: bytes) -> bytes:
    if len(chunk_id) != 4:
        raise ValueError("RIFF chunk IDs must contain exactly four bytes")
    return chunk_id


@dataclass(slots=True)
class RiffChunk:
    """A RIFF chunk, including its original alignment byte."""

    chunk_id: bytes
    data: bytes
    pad: bytes = b""

    def __post_init__(self) -> None:
        self.chunk_id = _checked_chunk_id(bytes(self.chunk_id))
        self.data = bytes(self.data)
        self.pad = bytes(self.pad)
        if self.pad not in (b"",) and len(self.pad) != 1:
            raise ValueError("a RIFF alignment pad must be zero or one byte")

    def to_bytes(self) -> bytes:
        pad = self.pad if len(self.data) & 1 else b""
        if len(self.data) & 1 and not pad:
            pad = b"\0"
        return self.chunk_id + struct.pack("<I", len(self.data)) + self.data + pad


def parse_chunks(data: bytes, *, context: str = "RIFF") -> list[RiffChunk]:
    """Parse an exact RIFF chunk stream."""

    chunks: list[RiffChunk] = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < 8:
            raise FormatError(f"{context}: {remaining} trailing byte(s) after final chunk")
        chunk_id, size = struct.unpack_from("<4sI", data, offset)
        body_start = offset + 8
        body_end = body_start + size
        padded_end = body_end + (size & 1)
        if body_end > len(data):
            label = chunk_id.decode("ascii", "replace")
            raise FormatError(
                f"{context}: chunk {label!r} declares {size} bytes beyond its container"
            )
        if padded_end > len(data):
            raise FormatError(f"{context}: odd-sized chunk is missing its alignment byte")
        chunks.append(
            RiffChunk(
                chunk_id=chunk_id,
                data=data[body_start:body_end],
                pad=data[body_end:padded_end],
            )
        )
        offset = padded_end
    return chunks


@dataclass(slots=True)
class WaveFile:
    """A WAVE form that preserves known and unknown chunks in order."""

    chunks: list[RiffChunk]

    @classmethod
    def from_bytes(cls, data: bytes) -> WaveFile:
        if len(data) < 12:
            raise FormatError("RIFF/WAVE data is shorter than its 12-byte header")
        magic, declared_size, form = struct.unpack_from("<4sI4s", data)
        if magic != b"RIFF":
            raise FormatError(f"expected RIFF magic, got {magic!r}")
        if form != b"WAVE":
            raise FormatError(f"expected WAVE form, got {form!r}")
        actual_size = len(data) - 8
        if declared_size != actual_size:
            raise FormatError(
                f"RIFF length mismatch: header says {declared_size}, actual is {actual_size}"
            )
        chunks = parse_chunks(data[12:], context="WAVE")
        wave = cls(chunks)
        wave.validate()
        return wave

    def to_bytes(self) -> bytes:
        body = b"WAVE" + b"".join(chunk.to_bytes() for chunk in self.chunks)
        return b"RIFF" + struct.pack("<I", len(body)) + body

    def chunks_with_id(self, chunk_id: bytes) -> list[RiffChunk]:
        return [chunk for chunk in self.chunks if chunk.chunk_id == chunk_id]

    def get_chunk(self, chunk_id: bytes) -> RiffChunk | None:
        return next(
            (chunk for chunk in self.chunks if chunk.chunk_id == chunk_id),
            None,
        )

    def require_chunk(self, chunk_id: bytes) -> RiffChunk:
        chunk = self.get_chunk(chunk_id)
        if chunk is None:
            raise FormatError(f"WAVE is missing required {chunk_id!r} chunk")
        return chunk

    def validate(self) -> None:
        fmt_chunks = self.chunks_with_id(b"fmt ")
        data_chunks = self.chunks_with_id(b"data")
        if len(fmt_chunks) != 1 or len(data_chunks) != 1:
            raise FormatError("WAVE must contain exactly one fmt and one data chunk")
        if self.chunks.index(fmt_chunks[0]) > self.chunks.index(data_chunks[0]):
            raise FormatError("WAVE fmt chunk must precede the data chunk")
        fmt = fmt_chunks[0].data
        if len(fmt) < 16:
            raise FormatError("WAVE fmt chunk must contain at least 16 bytes")
        format_tag, channels, sample_rate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
        if format_tag != 1:
            raise FormatError(f"unsupported WAVE format tag {format_tag}; PCM is required")
        if channels not in (1, 2):
            raise FormatError("WAVE must be mono or stereo")
        if sample_rate == 0 or block_align == 0 or bits == 0:
            raise FormatError("WAVE fmt contains a zero sample rate/alignment/bit depth")
        if len(self.require_chunk(b"data").data) % block_align:
            raise FormatError("WAVE data length is not a multiple of block alignment")
        korg = self.require_chunk(b"korg")
        esli = [
            chunk for chunk in parse_chunks(korg.data, context="korg") if chunk.chunk_id == b"esli"
        ]
        if len(esli) != 1:
            raise FormatError("korg must contain exactly one esli chunk")
        if len(esli[0].data) != EsliCodec.SIZE:
            raise FormatError(f"korg/esli must be {EsliCodec.SIZE} bytes, got {len(esli[0].data)}")

    def esli_chunk(self) -> RiffChunk:
        korg = self.require_chunk(b"korg")
        return next(
            chunk for chunk in parse_chunks(korg.data, context="korg") if chunk.chunk_id == b"esli"
        )

    def replace_esli(self, payload: bytes) -> None:
        if len(payload) != EsliCodec.SIZE:
            raise ValueError(f"esli payload must be {EsliCodec.SIZE} bytes")
        korg = self.require_chunk(b"korg")
        children = parse_chunks(korg.data, context="korg")
        matches = [chunk for chunk in children if chunk.chunk_id == b"esli"]
        if len(matches) != 1:
            raise FormatError("korg must contain exactly one esli chunk")
        matches[0].data = payload
        korg.data = b"".join(chunk.to_bytes() for chunk in children)

    @property
    def pcm_format(self) -> tuple[int, int, int]:
        """Return ``(channels, sample_rate, block_align)``."""

        _, channels, sample_rate, _, block_align, _ = struct.unpack_from(
            "<HHIIHH", self.require_chunk(b"fmt ").data
        )
        return channels, sample_rate, block_align


class EsliCodec:
    """Read and update the fixed-size KORG Electribe metadata block."""

    SIZE = 1172
    SLICE_COUNT = 64
    USER_SLOT_START = 501
    FACTORY_IMPORT_NUMBERS = tuple(
        number
        for start, stop in (
            (50, 86),
            (87, 113),
            (114, 126),
            (127, 136),
            (137, 182),
            (183, 184),
            (185, 186),
            (187, 189),
            (190, 461),
        )
        for number in range(start, stop)
    )

    @classmethod
    def decode(cls, raw: bytes) -> SampleMetadata:
        if len(raw) != cls.SIZE:
            raise FormatError(f"esli payload must be {cls.SIZE} bytes, got {len(raw)}")
        slot_index = struct.unpack_from("<H", raw, 0)[0]
        duplicate_index = struct.unpack_from("<H", raw, 78)[0]
        if slot_index != duplicate_index:
            raise FormatError("esli contains inconsistent duplicate slot indexes")
        slices = [
            Slice(*struct.unpack_from("<iIII", raw, 80 + index * 16))
            for index in range(cls.SLICE_COUNT)
        ]
        steps = list(struct.unpack_from("<64b", raw, 1104))
        name_bytes = raw[2:18].split(b"\0", 1)[0]
        return SampleMetadata(
            slot=slot_index + 1,
            name=name_bytes.decode("ascii", "replace").rstrip(),
            category=struct.unpack_from("<H", raw, 18)[0],
            start=struct.unpack_from("<I", raw, 40)[0],
            loop_start=struct.unpack_from("<I", raw, 40)[0] + struct.unpack_from("<I", raw, 44)[0],
            end=struct.unpack_from("<I", raw, 48)[0],
            one_shot=bool(raw[52]),
            plus_12db=bool(raw[66]),
            sample_rate=struct.unpack_from("<I", raw, 72)[0],
            tune=struct.unpack_from("<b", raw, 77)[0],
            stereo=bool(raw[65]),
            slices=slices,
            steps=steps,
        )

    @classmethod
    def encode(
        cls,
        original: bytes,
        metadata: SampleMetadata,
        *,
        audio_size: int,
    ) -> bytes:
        if len(original) != cls.SIZE:
            raise FormatError(f"esli payload must be {cls.SIZE} bytes")
        cls._validate_metadata(metadata)
        raw = bytearray(original)
        slot_index = metadata.slot - 1
        struct.pack_into("<H", raw, 0, slot_index)
        struct.pack_into("<H", raw, 78, slot_index)
        if metadata.slot >= cls.USER_SLOT_START:
            # The device uses a second, non-slot import identifier.  Hacktribe
            # samples beginning at visible slot 501 must begin at import 550.
            struct.pack_into("<H", raw, 20, metadata.slot + 49)
        elif 19 <= metadata.slot < 19 + len(cls.FACTORY_IMPORT_NUMBERS):
            struct.pack_into(
                "<H",
                raw,
                20,
                cls.FACTORY_IMPORT_NUMBERS[metadata.slot - 19],
            )
        try:
            encoded_name = metadata.name.encode("ascii", "strict")
        except UnicodeEncodeError as exc:
            raise FormatError("sample name must contain only ASCII characters") from exc
        if len(encoded_name) > 16:
            raise FormatError("sample name exceeds 16 ASCII bytes")
        raw[2:18] = encoded_name.ljust(16, b"\0")
        struct.pack_into("<H", raw, 18, metadata.category)
        struct.pack_into("<I", raw, 40, metadata.start)
        struct.pack_into("<I", raw, 44, metadata.loop_start - metadata.start)
        struct.pack_into("<I", raw, 48, metadata.end)
        raw[52] = int(metadata.one_shot)
        struct.pack_into("<I", raw, 60, audio_size)
        raw[65] = int(metadata.stereo)
        raw[66] = int(metadata.plus_12db)
        struct.pack_into("<I", raw, 72, metadata.sample_rate)
        struct.pack_into("<b", raw, 77, metadata.tune)
        for index, item in enumerate(metadata.slices):
            struct.pack_into(
                "<iIII",
                raw,
                80 + index * 16,
                item.start,
                item.length,
                item.attack_length,
                item.amplitude,
            )
        struct.pack_into("<64b", raw, 1104, *metadata.steps)
        return bytes(raw)

    @classmethod
    def _validate_metadata(cls, metadata: SampleMetadata) -> None:
        if not 1 <= metadata.slot <= 1020:
            raise FormatError(f"slot {metadata.slot} is outside the 1..1020 pointer table")
        if not 0 <= metadata.category <= 0xFFFF:
            raise FormatError("category must fit an unsigned 16-bit value")
        if not 0 <= metadata.sample_rate <= 0xFFFFFFFF:
            raise FormatError("sample rate must fit an unsigned 32-bit value")
        if not -128 <= metadata.tune <= 127:
            raise FormatError("tune must fit a signed byte")
        if len(metadata.slices) != cls.SLICE_COUNT or len(metadata.steps) != cls.SLICE_COUNT:
            raise FormatError("esli requires exactly 64 slices and 64 steps")
        if any(not -128 <= step <= 127 for step in metadata.steps):
            raise FormatError("slice steps must fit signed bytes")
        for label, value in (
            ("start", metadata.start),
            ("loop start", metadata.loop_start),
            ("end", metadata.end),
        ):
            if not 0 <= value <= 0xFFFFFFFF:
                raise FormatError(f"{label} must fit an unsigned 32-bit value")
        for index, item in enumerate(metadata.slices):
            if not -(2**31) <= item.start < 2**31:
                raise FormatError(f"slice {index} start must fit a signed 32-bit value")
            for label, value in (
                ("length", item.length),
                ("attack length", item.attack_length),
                ("amplitude", item.amplitude),
            ):
                if not 0 <= value <= 0xFFFFFFFF:
                    raise FormatError(f"slice {index} {label} must fit an unsigned 32-bit value")


def riff_length_at(data: bytes, offset: int) -> int:
    """Return a validated RIFF byte length at *offset*."""

    if offset < 0 or offset + 12 > len(data):
        raise FormatError(f"RIFF pointer {offset} lies outside the file")
    if data[offset : offset + 4] != b"RIFF":
        raise FormatError(f"pointer {offset} does not reference a RIFF chunk")
    declared = struct.unpack_from("<I", data, offset + 4)[0]
    total = declared + 8
    if total < 12 or offset + total > len(data):
        raise FormatError(f"RIFF at {offset} extends beyond the file")
    return total
