"""Stateful library editing with validation and undo/redo."""

from __future__ import annotations

import copy
import hashlib
import math
import struct
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from e2sample_manager.domain.models import Sample
from e2sample_manager.domain.profiles import (
    HACKTRIBE_PROFILE,
    KORG_PROFILE,
    DeviceProfile,
)
from e2sample_manager.formats.e2s_all import E2SAllFile
from e2sample_manager.formats.patterns import PatternFile
from e2sample_manager.formats.riff import EsliCodec, FormatError

from .errors import ServiceError
from .sample_io import export_sample, export_samples, import_sample

T = TypeVar("T")
Snapshot = tuple[E2SAllFile, DeviceProfile, int, PatternFile | None]


class LibraryService:
    """Own a working library and expose atomic editing operations."""

    def __init__(
        self,
        library: E2SAllFile | None = None,
        *,
        profile: DeviceProfile = KORG_PROFILE,
        history_limit: int = 100,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.library = library or E2SAllFile()
        self.profile = profile
        self.path: Path | None = None
        self.history_limit = history_limit
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []
        self._state_id = 0
        self._saved_state_id = 0
        self._next_state_id = 1
        self.patterns: PatternFile | None = None
        self.pattern_path: Path | None = None
        self._saved_patterns: bytes | None = None

    @property
    def dirty(self) -> bool:
        return self.sample_dirty or self.pattern_dirty

    @property
    def sample_dirty(self) -> bool:
        return self._state_id != self._saved_state_id

    @property
    def pattern_dirty(self) -> bool:
        return self.patterns is not None and self.patterns.to_bytes() != self._saved_patterns

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _snapshot(self) -> Snapshot:
        return (
            copy.deepcopy(self.library),
            self.profile,
            self._state_id,
            copy.deepcopy(self.patterns),
        )

    def _restore(self, snapshot: Snapshot) -> None:
        self.library, self.profile, self._state_id, self.patterns = copy.deepcopy(snapshot)

    def _change(self, operation: Callable[[], T], *, sample_change: bool = True) -> T:
        before = self._snapshot()
        try:
            result = operation()
        except Exception:
            self._restore(before)
            raise
        self._undo.append(before)
        size = max(1, self.library.memory_size + (len(self.patterns.data) if self.patterns else 0))
        limit = max(1, min(self.history_limit, 128 * 1024 * 1024 // size))
        del self._undo[:-limit]
        self._redo.clear()
        if sample_change:
            self._state_id = self._next_state_id
            self._next_state_id += 1
        return result

    def new(self, *, profile: DeviceProfile | None = None) -> E2SAllFile:
        self.library = E2SAllFile()
        if profile is not None:
            self.profile = profile
        self.path = None
        self.patterns = None
        self.pattern_path = None
        self._saved_patterns = None
        self._undo.clear()
        self._redo.clear()
        self._state_id = self._next_state_id
        self._next_state_id += 1
        self._saved_state_id = self._state_id
        return self.library

    def open(self, path: str | Path) -> E2SAllFile:
        source = Path(path)
        try:
            library = E2SAllFile.load(source)
        except FormatError as exc:
            raise ServiceError(f"Bibliothek '{source}' kann nicht geöffnet werden: {exc}") from exc
        self.library = library
        self.path = source
        self.patterns = None
        self.pattern_path = None
        self._saved_patterns = None
        self._undo.clear()
        self._redo.clear()
        self._state_id = self._next_state_id
        self._next_state_id += 1
        self._saved_state_id = self._state_id
        return library

    def save(self, *, backup: bool = True) -> Path:
        if self.path is None:
            raise ServiceError("Die Bibliothek hat noch keinen Speicherort.")
        return self._save_to(self.path, backup=backup)

    def save_as(self, path: str | Path, *, backup: bool = True) -> Path:
        target = Path(path)
        result = self._save_to(target, backup=backup)
        self.path = target
        return result

    def _save_to(self, path: Path, *, backup: bool) -> Path:
        if self.pattern_path and path.resolve() == self.pattern_path.resolve():
            raise ServiceError("Sample-Bank und Patterns benötigen unterschiedliche Dateien.")
        try:
            self.library.validate(self.profile)
            self.library.save_atomic(path, backup=backup)
        except FormatError as exc:
            raise ServiceError(f"Bibliothek kann nicht gespeichert werden: {exc}") from exc
        self._saved_state_id = self._state_id
        return path

    def update_metadata(self, source_slot: int, **values: Any) -> Sample:
        """Atomically update editable metadata and retain undo support."""

        if "sample_rate" in values:
            raise ServiceError(
                "Die Samplerate ist schreibgeschützt. Audio bei Bedarf neu importieren."
            )
        sample = self.require_sample(source_slot)

        def operation() -> Sample:
            metadata = sample.metadata
            old_slot = metadata.slot
            target = values.get("slot", old_slot)
            if target != old_slot:
                if not self.profile.accepts_slot(target):
                    raise ServiceError(f"Slot {target} ist für {self.profile.name} ungültig.")
                if self.library.by_slot(target) is not None:
                    raise ServiceError(f"Slot {target} ist bereits belegt.")
            for name, value in values.items():
                if not hasattr(metadata, name):
                    raise ServiceError(f"Unbekanntes Metadatenfeld: {name}")
                setattr(metadata, name, value)
            if metadata.slot != old_slot:
                self.library.samples.sort(key=lambda item: item.metadata.slot)
                self._remap_patterns({old_slot: metadata.slot})
            EsliCodec._validate_metadata(metadata)
            return sample

        return self._change(operation)

    def _free_slots(self, *, first: int | None = None) -> list[int]:
        occupied = {sample.metadata.slot for sample in self.library.samples}
        minimum = self.profile.user_slot_start if first is None else first
        return [slot for slot in self.profile.slots_from(minimum) if slot not in occupied]

    def merge_all(self, path: str | Path) -> list[Sample]:
        try:
            incoming = E2SAllFile.load(path)
        except FormatError as exc:
            raise ServiceError(
                f"Bibliothek '{path}' kann nicht zusammengeführt werden: {exc}"
            ) from exc

        def operation() -> list[Sample]:
            added: list[Sample] = []
            for original in incoming.samples:
                sample = copy.deepcopy(original)
                occupied = self.library.by_slot(sample.metadata.slot)
                if occupied is not None or not self.profile.accepts_slot(sample.metadata.slot):
                    free = self._free_slots()
                    if not free:
                        raise ServiceError("Keine freien Sample-Slots für die Zusammenführung.")
                    sample.metadata.slot = free[0]
                self.library.samples.append(sample)
                added.append(sample)
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            return added

        return self._change(operation)

    merge = merge_all

    def import_samples(
        self,
        paths: Iterable[str | Path],
        *,
        first_slot: int | None = None,
        mono: bool = False,
        left: float = 0.5,
        right: float = 0.5,
    ) -> list[Sample]:
        sources = list(paths)

        def operation() -> list[Sample]:
            free = self._free_slots(first=first_slot)
            if len(free) < len(sources):
                raise ServiceError("Nicht genügend freie Sample-Slots für den Import.")
            added = [
                import_sample(
                    source,
                    slot=slot,
                    mono=mono,
                    left=left,
                    right=right,
                )
                for source, slot in zip(sources, free[: len(sources)], strict=True)
            ]
            self.library.samples.extend(added)
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            return added

        return self._change(operation)

    import_files = import_samples

    def replace(
        self,
        slot: int,
        path: str | Path,
        *,
        mono: bool = False,
        left: float = 0.5,
        right: float = 0.5,
    ) -> Sample:
        old = self.require_sample(slot)

        def operation() -> Sample:
            replacement = import_sample(
                path,
                slot=slot,
                name=old.metadata.name,
                mono=mono,
                left=left,
                right=right,
                category=old.metadata.category,
            )
            index = self.library.samples.index(old)
            self.library.samples[index] = replacement
            return replacement

        return self._change(operation)

    def require_sample(self, slot: int) -> Sample:
        sample = self.library.by_slot(slot)
        if sample is None:
            raise ServiceError(f"Slot {slot} enthält kein Sample.")
        return sample

    def delete(self, slots: int | Iterable[int]) -> list[Sample]:
        selected = {slots} if isinstance(slots, int) else set(slots)

        def operation() -> list[Sample]:
            removed = [
                sample for sample in self.library.samples if sample.metadata.slot in selected
            ]
            if len(removed) != len(selected):
                missing = sorted(selected - {sample.metadata.slot for sample in removed})
                raise ServiceError(f"Leere oder unbekannte Slots: {missing}")
            self.library.samples[:] = [
                sample for sample in self.library.samples if sample.metadata.slot not in selected
            ]
            return removed

        return self._change(operation)

    def duplicate(self, slot: int, target_slot: int | None = None) -> Sample:
        source = self.require_sample(slot)

        def operation() -> Sample:
            target = target_slot
            if target is None:
                free = self._free_slots(first=slot + 1)
                if not free:
                    free = self._free_slots()
                if not free:
                    raise ServiceError("Kein freier Slot zum Duplizieren.")
                target = free[0]
            self._ensure_free_valid(target)
            duplicate = copy.deepcopy(source)
            duplicate.metadata.slot = target
            self.library.samples.append(duplicate)
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            return duplicate

        return self._change(operation)

    def _ensure_free_valid(self, slot: int) -> None:
        if not self.profile.accepts_slot(slot):
            raise ServiceError(f"Slot {slot} ist für {self.profile.name} ungültig.")
        if self.library.by_slot(slot) is not None:
            raise ServiceError(f"Slot {slot} ist bereits belegt.")

    def move(self, source_slot: int, target_slot: int) -> Sample:
        sample = self.require_sample(source_slot)

        def operation() -> Sample:
            self._ensure_free_valid(target_slot)
            sample.metadata.slot = target_slot
            self._remap_patterns({source_slot: target_slot})
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            return sample

        return self._change(operation)

    def swap(self, first_slot: int, second_slot: int) -> None:
        first = self.require_sample(first_slot)
        second = self.require_sample(second_slot)

        def operation() -> None:
            first.metadata.slot, second.metadata.slot = second.metadata.slot, first.metadata.slot
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            self._remap_patterns({first_slot: second_slot, second_slot: first_slot})

        self._change(operation)

    def fill_gaps(self, first_slot: int | None = None) -> list[int]:
        start = self.profile.user_slot_start if first_slot is None else first_slot

        def operation() -> list[int]:
            samples = sorted(self.library.samples, key=lambda item: item.metadata.slot)
            slots = self.profile.slots_from(start)
            if len(slots) < len(samples):
                raise ServiceError(f"Die Samples passen ab Slot {start} nicht in das Profil.")
            mapping = {
                sample.metadata.slot: slot for sample, slot in zip(samples, slots, strict=False)
            }
            for sample, slot in zip(samples, slots, strict=False):
                sample.metadata.slot = slot
            self._remap_patterns(mapping)
            self.library.samples[:] = samples
            return slots[: len(samples)]

        return self._change(operation)

    def trim(self, slots: int | Iterable[int]) -> list[Sample]:
        selected = [slots] if isinstance(slots, int) else list(slots)

        def operation() -> list[Sample]:
            trimmed: list[Sample] = []
            for slot in selected:
                sample = self.require_sample(slot)
                _, _, block_align = sample.wave.pcm_format
                metadata = sample.metadata
                begin = metadata.start - (metadata.start % block_align)
                final = min(
                    begin + metadata.end + block_align,
                    len(sample.audio_data),
                )
                if final <= begin:
                    raise ServiceError(f"Slot {slot} besitzt keinen gültigen Trim-Bereich.")
                sample.audio_data = sample.audio_data[begin:final]
                metadata.loop_start = max(0, metadata.loop_start - begin)
                metadata.start = 0
                metadata.end = len(sample.audio_data) - block_align
                sample.wave.replace_esli(
                    EsliCodec.encode(
                        sample.wave.esli_chunk().data,
                        metadata,
                        audio_size=len(sample.audio_data),
                    )
                )
                trimmed.append(sample)
            return trimmed

        return self._change(operation)

    def set_profile(self, profile: DeviceProfile) -> None:
        self._change(lambda: setattr(self, "profile", profile))

    def arrange_hacktribe(self, first_slot: int = 501) -> list[int]:
        def operation() -> list[int]:
            self.profile = HACKTRIBE_PROFILE
            self.library.samples.sort(key=lambda item: item.metadata.slot)
            targets = self.profile.slots_from(first_slot)
            if len(targets) < len(self.library.samples):
                raise ServiceError("Hacktribe: Nicht genügend freie Slots zwischen 501 und 999.")
            mapping = {
                s.metadata.slot: t for s, t in zip(self.library.samples, targets, strict=False)
            }
            for sample in self.library.samples:
                sample.metadata.slot = mapping[sample.metadata.slot]
            self._remap_patterns(mapping)
            return [sample.metadata.slot for sample in self.library.samples]

        return self._change(operation)

    def _remap_patterns(self, mapping: dict[int, int]) -> int:
        return self.patterns.remap(mapping) if self.patterns else 0

    def plan_move(self, start: int, end: int, target: int) -> dict[int, int]:
        if not 1 <= start <= end <= 1020:
            raise ServiceError("Quellbereich muss aufsteigend zwischen 1 und 1020 liegen.")
        selected = {
            s.metadata.slot for s in self.library.samples if start <= s.metadata.slot <= end
        }
        if not selected:
            raise ServiceError("Der Quellbereich enthält keine Samples.")
        mapping = {slot: target + slot - start for slot in sorted(selected)}
        invalid = [n for n in mapping.values() if not self.profile.accepts_slot(n)]
        if invalid:
            hint = "501–999" if self.profile == HACKTRIBE_PROFILE else "19–421 und 501–999"
            raise ServiceError(f"Ziel-Slots {invalid[:8]} sind reserviert. Erlaubt: {hint}.")
        occupied = {s.metadata.slot for s in self.library.samples} - selected
        conflicts = occupied.intersection(mapping.values())
        if conflicts:
            raise ServiceError(
                f"Ziel bereits belegt: {sorted(conflicts)[:12]}. Es wird nichts überschrieben."
            )
        if self.patterns and not self.patterns.sampler:
            raise ServiceError("Synth-Pattern geladen: Zuerst in das Sampler-Format konvertieren.")
        return mapping

    def move_range(self, start: int, end: int, target: int) -> dict[int, int]:
        mapping = self.plan_move(start, end, target)

        def operation() -> dict[int, int]:
            for sample in self.library.samples:
                sample.metadata.slot = mapping.get(sample.metadata.slot, sample.metadata.slot)
            self.library.samples.sort(key=lambda sample: sample.metadata.slot)
            self._remap_patterns(mapping)
            return mapping

        return self._change(operation)

    def convert_mono(self, slots: Iterable[int], *, left: float = 0.5, right: float = 0.5) -> None:
        import numpy as np

        from .sample_io import mix_to_mono

        def operation() -> None:
            for slot in slots:
                sample = self.require_sample(slot)
                channels, rate, _ = sample.wave.pcm_format
                if channels == 1:
                    continue
                frames = np.frombuffer(sample.audio_data, dtype="<i2").reshape(-1, 2)
                sample.audio_data = mix_to_mono(frames, left, right).tobytes()
                meta = sample.metadata
                meta.start //= 2
                meta.loop_start //= 2
                meta.end //= 2
                meta.stereo = False
                fmt = bytearray(sample.wave.require_chunk(b"fmt ").data)
                struct.pack_into("<HHIIHH", fmt, 0, 1, 1, rate, rate * 2, 2, 16)
                sample.wave.require_chunk(b"fmt ").data = bytes(fmt)

        self._change(operation)

    def normalize_samples(self, slots: Iterable[int]) -> int:
        """Normalize PCM peaks to -1 dBFS with linked stereo gain and one undo step."""
        import numpy as np

        updates = {}
        target = round(32767 * 10 ** (-1 / 20))
        for slot in dict.fromkeys(slots):
            sample = self.require_sample(slot)
            _ = sample.wave.pcm_format  # Validate PCM16 before processing.
            pcm = np.frombuffer(sample.audio_data, dtype="<i2").astype(np.float64)
            peak = np.max(np.abs(pcm), initial=0)
            if peak == 0:
                continue
            data = np.rint(pcm * (target / peak)).clip(-32768, 32767).astype("<i2").tobytes()
            if data != sample.audio_data:
                updates[slot] = data
        if updates:

            def operation() -> None:
                for slot, data in updates.items():
                    self.require_sample(slot).audio_data = data

            self._change(operation)
        return len(updates)

    def find_duplicate_audio(self) -> list[list[int]]:
        """Find equal PCM and format; names and playback markers may differ."""
        groups: dict[tuple, list[int]] = {}
        for sample in sorted(self.library.samples, key=lambda item: item.metadata.slot):
            key = (sample.wave.pcm_format, hashlib.sha256(sample.audio_data).digest())
            groups.setdefault(key, []).append(sample.metadata.slot)
        return [slots for slots in groups.values() if len(slots) > 1]

    def fade_samples(self, slots: Iterable[int], *, fade_in_ms: float, fade_out_ms: float) -> int:
        """Apply linked linear fades within the playback region, preserving all markers."""
        import numpy as np

        if any(not math.isfinite(ms) or not 0 <= ms <= 100 for ms in (fade_in_ms, fade_out_ms)):
            raise ServiceError("Ein-/Ausblendung muss zwischen 0 und 100 ms liegen.")
        updates = {}
        for slot in dict.fromkeys(slots):
            sample = self.require_sample(slot)
            channels, rate, align = sample.wave.pcm_format
            pcm = np.frombuffer(sample.audio_data, dtype="<i2").reshape(-1, channels)
            start = sample.metadata.start // align
            end = (sample.metadata.start + sample.metadata.end) // align + 1
            if not 0 <= start < end <= len(pcm):
                raise ServiceError(f"Slot {slot}: ungültiger Wiedergabebereich.")
            gain = np.ones(end - start)
            for ms, incoming in ((fade_in_ms, True), (fade_out_ms, False)):
                if ms == 0:
                    continue
                count = min(len(gain), max(2, round(rate * ms / 1000)))
                ramp = np.linspace(0, 1, count) if count > 1 else np.zeros(1)
                if incoming:
                    gain[:count] *= ramp
                else:
                    gain[-count:] *= ramp[::-1]
            result = pcm.copy()
            result[start:end] = np.rint(pcm[start:end].astype(float) * gain[:, None]).astype("<i2")
            data = result.astype("<i2").tobytes()
            if data != sample.audio_data:
                updates[slot] = data
        if updates:

            def operation() -> None:
                for slot, data in updates.items():
                    self.require_sample(slot).audio_data = data

            self._change(operation)
        return len(updates)

    def sample_usage(self, slots: Iterable[int]) -> dict[int, list[dict]]:
        """Report assignments, including muted/empty parts, in the loaded sampler file."""
        if self.patterns is None:
            raise ServiceError("Zuerst die passende Sampler-Pattern-Datei unter Patterns öffnen.")
        if not self.patterns.sampler:
            raise ServiceError("Synth-Patterns enthalten keine auswertbaren Sampler-Zuordnungen.")
        usage: dict[int, list[dict]] = {slot: [] for slot in slots}
        for index in range(self.patterns.count):
            for part in range(16):
                slot = self.patterns.oscillator(index, part)
                if slot in usage:
                    usage[slot].append(
                        {
                            "pattern": index + 1,
                            "name": self.patterns.info(index)["name"],
                            "part": part + 1,
                            "mute": self.patterns.part_info(index, part)["mute"],
                        }
                    )
        return usage

    def load_patterns(self, path: str | Path) -> None:
        patterns = PatternFile.load(path)
        self.patterns = patterns
        self.pattern_path = Path(path)
        self._saved_patterns = patterns.to_bytes()
        # A file load defines a new document boundary for the shared undo history.
        self._undo.clear()
        self._redo.clear()

    def new_pattern(self) -> None:
        self.patterns = PatternFile.new()
        self.pattern_path = None
        self._saved_patterns = None
        self._undo.clear()
        self._redo.clear()

    def edit_pattern(self, operation: Callable[[PatternFile], T]) -> T:
        if self.patterns is None:
            raise ServiceError("Zuerst ein Pattern öffnen oder neu anlegen.")
        return self._change(lambda: operation(self.patterns), sample_change=False)

    def save_patterns(self, path: str | Path) -> None:
        if self.patterns is None:
            raise ServiceError("Kein Pattern geladen.")
        target = Path(path)
        if self.path and target.resolve() == self.path.resolve():
            raise ServiceError("Pattern und Sample-Bank benötigen unterschiedliche Dateien.")
        self.patterns.save(target)
        self.pattern_path = target
        self._saved_patterns = self.patterns.to_bytes()

    def undo(self) -> bool:
        if not self._undo:
            return False
        current = self._snapshot()
        snapshot = self._undo.pop()
        self._redo.append(current)
        self._restore(snapshot)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        current = self._snapshot()
        snapshot = self._redo.pop()
        self._undo.append(current)
        self._restore(snapshot)
        return True

    def export(
        self,
        slot: int,
        path: str | Path,
        *,
        include_markers: bool = False,
    ) -> Path:
        return export_sample(
            self.require_sample(slot),
            path,
            include_markers=include_markers,
        )

    def export_all(
        self,
        directory: str | Path,
        *,
        include_markers: bool = False,
    ) -> list[Path]:
        return export_samples(
            self.library.samples,
            directory,
            include_markers=include_markers,
        )
