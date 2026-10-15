"""Typed domain objects for Electribe sample libraries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from e2sample_manager.formats.riff import WaveFile


@dataclass(slots=True)
class Slice:
    """One of the 64 Electribe slice descriptors."""

    start: int = 0
    length: int = 0
    attack_length: int = 0
    amplitude: int = 0


@dataclass(slots=True)
class SampleMetadata:
    """Editable metadata stored in the KORG ``esli`` chunk."""

    slot: int
    name: str = ""
    category: int = 17
    start: int = 0
    loop_start: int = 0
    end: int = 0
    one_shot: bool = True
    plus_12db: bool = False
    sample_rate: int = 44_100
    tune: int = 0
    stereo: bool = False
    slices: list[Slice] = field(default_factory=lambda: [Slice() for _ in range(64)])
    steps: list[int] = field(default_factory=lambda: [0] * 64)

    def __post_init__(self) -> None:
        if len(self.slices) != 64:
            raise ValueError("exactly 64 slices are required")
        if len(self.steps) != 64:
            raise ValueError("exactly 64 slice steps are required")


@dataclass(slots=True)
class Sample:
    """A sample's domain metadata and its lossless RIFF representation."""

    metadata: SampleMetadata
    wave: WaveFile

    @property
    def audio_data(self) -> bytes:
        return self.wave.require_chunk(b"data").data

    @audio_data.setter
    def audio_data(self, value: bytes) -> None:
        self.wave.require_chunk(b"data").data = bytes(value)

    @property
    def memory_size(self) -> int:
        return len(self.audio_data)


@dataclass(slots=True)
class Library:
    """An ordered collection of Electribe samples."""

    samples: list[Sample] = field(default_factory=list)

    @property
    def memory_size(self) -> int:
        return sum(sample.memory_size for sample in self.samples)

    def by_slot(self, slot: int) -> Sample | None:
        return next((sample for sample in self.samples if sample.metadata.slot == slot), None)
