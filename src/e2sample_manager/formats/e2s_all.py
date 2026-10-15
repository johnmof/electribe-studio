"""Reader and writer for KORG ``e2sSample.all`` libraries."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Self

from e2sample_manager.domain.models import Library, Sample
from e2sample_manager.domain.profiles import DeviceProfile
from e2sample_manager.formats.riff import (
    EsliCodec,
    FormatError,
    WaveFile,
    riff_length_at,
)

MAGIC = b"e2s sample all\x1a\x00"
HEADER_SIZE = 4096
POINTER_COUNT = 1020


class E2SAllFile(Library):
    """A complete ``e2sSample.all`` library."""

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Self:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise FormatError(f"cannot read {path!s}: {exc}") from exc
        return cls.from_bytes(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        if len(data) < HEADER_SIZE:
            raise FormatError(f"e2sSample.all is shorter than its {HEADER_SIZE}-byte header")
        if data[: len(MAGIC)] != MAGIC:
            raise FormatError("invalid e2sSample.all magic")
        pointers = struct.unpack_from(f"<{POINTER_COUNT}I", data, len(MAGIC))
        occupied: list[tuple[int, int, int]] = []
        samples: list[Sample] = []
        seen_pointers: set[int] = set()

        for slot_index, pointer in enumerate(pointers):
            if pointer == 0:
                continue
            slot = slot_index + 1
            if pointer < HEADER_SIZE:
                raise FormatError(f"slot {slot} pointer {pointer} points inside the header")
            if pointer in seen_pointers:
                raise FormatError(f"duplicate RIFF pointer {pointer} in slot {slot}")
            seen_pointers.add(pointer)
            total = riff_length_at(data, pointer)
            end = pointer + total
            occupied.append((pointer, end, slot))
            wave = WaveFile.from_bytes(data[pointer:end])
            metadata = EsliCodec.decode(wave.esli_chunk().data)
            if metadata.slot != slot:
                raise FormatError(
                    f"pointer-table slot {slot} disagrees with esli slot {metadata.slot}"
                )
            samples.append(Sample(metadata=metadata, wave=wave))

        occupied.sort()
        for previous, current in zip(occupied, occupied[1:], strict=False):
            if current[0] < previous[1]:
                raise FormatError(f"RIFF payloads for slots {previous[2]} and {current[2]} overlap")
        return cls(samples=samples)

    def to_bytes(self) -> bytes:
        self._validate_common()
        pointers = [0] * POINTER_COUNT
        payloads: list[bytes] = []
        next_offset = HEADER_SIZE
        for sample in sorted(self.samples, key=lambda item: item.metadata.slot):
            sample.wave.replace_esli(
                EsliCodec.encode(
                    sample.wave.esli_chunk().data,
                    sample.metadata,
                    audio_size=sample.memory_size,
                )
            )
            payload = sample.wave.to_bytes()
            pointers[sample.metadata.slot - 1] = next_offset
            payloads.append(payload)
            next_offset += len(payload)
        header = MAGIC + struct.pack(f"<{POINTER_COUNT}I", *pointers)
        if len(header) != HEADER_SIZE:
            raise AssertionError("internal e2sSample.all header-size error")
        return header + b"".join(payloads)

    def validate(self, profile: DeviceProfile) -> None:
        self._validate_common()
        for sample in self.samples:
            slot = sample.metadata.slot
            if not profile.accepts_slot(slot):
                raise FormatError(f"slot {slot} is not valid for {profile.name}")
            self._validate_sample(sample)
        if self.memory_size > profile.memory_limit:
            raise FormatError(
                f"sample data uses {self.memory_size} bytes, exceeding "
                f"{profile.name}'s {profile.memory_limit}-byte limit"
            )

    def arrange_from_slot(self, first_slot: int = 501) -> Self:
        if not 1 <= first_slot <= POINTER_COUNT:
            raise FormatError(f"first slot must be in 1..{POINTER_COUNT}")
        last_slot = first_slot + len(self.samples) - 1
        if last_slot > POINTER_COUNT:
            raise FormatError(f"{len(self.samples)} samples do not fit from slot {first_slot}")
        for slot, sample in enumerate(self.samples, start=first_slot):
            sample.metadata.slot = slot
        return self

    def save_atomic(
        self,
        path: str | os.PathLike[str],
        *,
        backup: bool = True,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.to_bytes()
        temporary: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if backup and target.exists():
                self._write_backup(target)
            os.replace(temporary, target)
            temporary = None
            # Windows cannot open directories with os.open. The file itself has
            # already been flushed before replacement on both platforms.
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise FormatError(f"cannot atomically save {target}: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_backup(target: Path) -> None:
        backup_path = target.with_name(target.name + ".bak")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{backup_path.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(target, temporary)
            os.replace(temporary, backup_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_common(self) -> None:
        slots: set[int] = set()
        for sample in self.samples:
            slot = sample.metadata.slot
            if not 1 <= slot <= POINTER_COUNT:
                raise FormatError(f"slot {slot} is outside the 1..{POINTER_COUNT} table")
            if slot in slots:
                raise FormatError(f"duplicate sample slot {slot}")
            slots.add(slot)
            self._validate_sample(sample)

    @staticmethod
    def _validate_sample(sample: Sample) -> None:
        sample.wave.validate()
        metadata = sample.metadata
        EsliCodec._validate_metadata(metadata)
        channels, sample_rate, block_align = sample.wave.pcm_format
        if metadata.stereo != (channels == 2):
            raise FormatError(f"slot {metadata.slot} stereo metadata disagrees with WAVE channels")
        if metadata.sample_rate != sample_rate:
            raise FormatError(f"slot {metadata.slot} sample rate disagrees with WAVE fmt")
        if not sample.audio_data:
            raise FormatError(f"slot {metadata.slot} contains empty sample data")
        final_byte = len(sample.audio_data) - block_align
        if metadata.start > final_byte:
            raise FormatError(f"slot {metadata.slot} start lies beyond sample data")
        if metadata.loop_start < metadata.start:
            raise FormatError(f"slot {metadata.slot} loop start precedes start")
        if metadata.loop_start > metadata.start + metadata.end:
            raise FormatError(f"slot {metadata.slot} loop start follows end")
        if metadata.start + metadata.end > final_byte:
            raise FormatError(f"slot {metadata.slot} end lies beyond sample data")
