"""Conservative, byte-preserving Electribe 2 pattern and bank editor.

Offsets are documented in docs/FORMATS.md. Only explicitly edited bytes change;
motion data, effects, globals and reserved bytes survive a load/save cycle.
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path

from .e2s_all import E2SAllFile
from .riff import FormatError

PATTERN_SIZE = 0x4000
SINGLE_OFFSET = 0x100
BANK_OFFSET = 0x10100
BANK_COUNT = 250
PART_OFFSET = 0x800
PART_SIZE = 0x330
STEP_OFFSET = 0x30
STEP_SIZE = 12


class PatternFile:
    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)
        if len(data) == SINGLE_OFFSET + PATTERN_SIZE:
            self.offset, self.count = SINGLE_OFFSET, 1
        elif len(data) == BANK_OFFSET + BANK_COUNT * PATTERN_SIZE:
            self.offset, self.count = BANK_OFFSET, BANK_COUNT
        else:
            raise FormatError(
                "Ungültige Pattern-Dateigröße (Einzelpattern oder 250er-Bank erwartet)."
            )
        if data[:4] != b"KORG" or data[16:32].rstrip(b"\0") not in (b"e2sampler", b"electribe"):
            raise FormatError("Keine Electribe-2-Pattern-Datei: KORG/Gerätekennung fehlt.")
        for index in range(self.count):
            start = self.base(index)
            if (
                data[start : start + 4] != b"PTST"
                or data[start + 0x3BFC : start + 0x3C00] != b"PTED"
            ):
                raise FormatError(f"Pattern {index + 1}: PTST/PTED-Kennung fehlt.")

    @classmethod
    def load(cls, path: str | Path) -> PatternFile:
        return cls(Path(path).read_bytes())

    @classmethod
    def new(cls) -> PatternFile:
        """Build a sampler pattern with explicit neutral defaults, no factory content."""
        header = bytearray(b"\xff" * SINGLE_OFFSET)
        header[:32] = b"KORG".ljust(16, b"\0") + b"e2sampler".ljust(16, b"\0")
        header[32:36] = b"\x01\0\0\0"
        block = bytearray(PATTERN_SIZE)
        block[:4], block[0x3BFC:0x3C00] = b"PTST", b"PTED"
        block[16:20] = b"Init"
        struct.pack_into("<H", block, 34, 1200)
        block[37], block[41], block[42] = 3, 2, 100
        for part in range(16):
            start = PART_OFFSET + part * PART_SIZE
            block[start + 2 : start + 5] = bytes((3, 1, 1))
            struct.pack_into("<H", block, start + 8, 500)
            block[start + 13], block[start + 21], block[start + 24] = 127, 127, 100
            for step in range(64):
                point = start + STEP_OFFSET + step * STEP_SIZE
                block[point + 1 : point + 3] = bytes((72, 100))
        return cls(bytes(header + block))

    @property
    def sampler(self) -> bool:
        return self.data[16:25] == b"e2sampler"

    def to_bytes(self) -> bytes:
        return bytes(self.data)

    def base(self, index: int) -> int:
        if not 0 <= index < self.count:
            raise ValueError("Pattern-Index außerhalb des Bereichs.")
        return self.offset + index * PATTERN_SIZE

    def part_base(self, index: int, part: int) -> int:
        if not 0 <= part < 16:
            raise ValueError("Part muss zwischen 1 und 16 liegen.")
        return self.base(index) + PART_OFFSET + part * PART_SIZE

    def info(self, index: int) -> dict:
        b = self.base(index)
        return {
            "name": bytes(self.data[b + 16 : b + 34]).split(b"\0")[0].decode("ascii", "replace"),
            "tempo": struct.unpack_from("<H", self.data, b + 34)[0] / 10,
            "swing": struct.unpack_from("b", self.data, b + 36)[0],
            "length": self.data[b + 37] + 1,
        }

    def set_info(self, index: int, *, name: str, tempo: float, swing: int, length: int) -> None:
        encoded = name.encode("ascii")
        if (
            len(encoded) > 16
            or not 20 <= tempo <= 300
            or not -50 <= swing <= 50
            or not 1 <= length <= 4
        ):
            raise ValueError("Name: 16 ASCII-Zeichen; Tempo: 20–300; Swing: -50–50; Länge: 1–4.")
        b = self.base(index)
        self.data[b + 16 : b + 32] = encoded.ljust(16, b"\0")
        struct.pack_into("<H", self.data, b + 34, round(tempo * 10))
        struct.pack_into("b", self.data, b + 36, swing)
        self.data[b + 37] = length - 1

    def oscillator(self, index: int, part: int) -> int:
        return struct.unpack_from("<H", self.data, self.part_base(index, part) + 8)[0] + 1

    def set_oscillator(self, index: int, part: int, slot: int) -> None:
        if not 1 <= slot <= 999:
            raise ValueError("Oszillator/Slot muss zwischen 1 und 999 liegen.")
        struct.pack_into("<H", self.data, self.part_base(index, part) + 8, slot - 1)

    def part_info(self, index: int, part: int) -> dict:
        b = self.part_base(index, part)
        return {
            "slot": self.oscillator(index, part),
            "level": self.data[b + 24],
            "pan": struct.unpack_from("b", self.data, b + 25)[0],
            "mute": bool(self.data[b + 1]),
            "last_step": self.data[b] or 16,
        }

    def set_part(
        self, index: int, part: int, *, slot: int, level: int, pan: int, mute: bool, last_step: int
    ) -> None:
        if not 0 <= level <= 127 or not -63 <= pan <= 63 or not 1 <= last_step <= 16:
            raise ValueError("Ungültiger Part-Wert.")
        self.set_oscillator(index, part, slot)
        b = self.part_base(index, part)
        self.data[b], self.data[b + 1], self.data[b + 24] = last_step % 16, int(mute), level
        struct.pack_into("b", self.data, b + 25, pan)

    def step_base(self, index: int, part: int, step: int) -> int:
        if not 0 <= step < 64:
            raise ValueError("Step muss zwischen 1 und 64 liegen.")
        return self.part_base(index, part) + STEP_OFFSET + step * STEP_SIZE

    def step(self, index: int, part: int, step: int) -> dict:
        b = self.step_base(index, part, step)
        return {
            "on": bool(self.data[b]),
            "gate": self.data[b + 1],
            "velocity": self.data[b + 2],
            "trigger": bool(self.data[b + 3]),
            "notes": [value - 1 for value in self.data[b + 4 : b + 8]],
        }

    def set_step(
        self,
        index: int,
        part: int,
        step: int,
        *,
        on: bool,
        gate: int,
        velocity: int,
        trigger: bool,
        notes: list[int],
    ) -> None:
        if not 0 <= gate <= 127 or not 0 <= velocity <= 127 or len(notes) != 4:
            raise ValueError("Gate/Velocity: 0–127; genau vier Notenplätze erforderlich.")
        if any(not -1 <= note <= 127 for note in notes):
            raise ValueError("Noten: -1 (aus) oder MIDI 0–127.")
        b = self.step_base(index, part, step)
        self.data[b : b + 8] = bytes(
            (int(on), gate, velocity, int(trigger), *(n + 1 for n in notes))
        )

    def move_step(self, index: int, part: int, source: int, target: int) -> None:
        """Move one active step/chord; keep motion and reserved bytes at their positions."""
        self.step_base(index, part, source)
        self.step_base(index, part, target)
        if source == target:
            return
        playable = [
            bar * 16 + step
            for bar in range(self.info(index)["length"])
            for step in range(self.part_info(index, part)["last_step"])
        ]
        if source not in playable or target not in playable:
            raise ValueError("Quelle/Ziel liegen außerhalb von Pattern-Länge oder Last Step.")
        values = self.step(index, part, source)
        if not values["on"]:
            raise ValueError("Der gewählte Quell-Step ist nicht aktiv.")
        if self.step(index, part, target)["on"]:
            raise ValueError("Der Ziel-Step ist belegt. Es wird nichts überschrieben.")
        for step in (source, target):
            previous = playable[(playable.index(step) - 1) % len(playable)]
            before = self.step(index, part, previous)
            if (before["on"] and before["gate"] == 127) or (
                step == source and values["gate"] == 127
            ):
                raise ValueError("Tie-Verbindungen werden nicht einzeln verschoben.")
        self.set_step(index, part, target, **values)
        cleared = dict(values, on=False, trigger=False, notes=[-1] * 4)
        self.set_step(index, part, source, **cleared)

    def remap(self, mapping: dict[int, int]) -> int:
        if not self.sampler:
            raise ValueError(
                "Synth-Patterns haben andere Oszillator-Zuordnungen. Zuerst konvertieren."
            )
        changed = 0
        for index in range(self.count):
            for part in range(16):
                old = self.oscillator(index, part)
                if old in mapping and old != mapping[old]:
                    self.set_oscillator(index, part, mapping[old])
                    changed += 1
        return changed

    def extract(self, index: int) -> PatternFile:
        start = self.base(index)
        return PatternFile(bytes(self.data[:256] + self.data[start : start + PATTERN_SIZE]))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(self.data)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                E2SAllFile._write_backup(target)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
