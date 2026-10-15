"""Core API for e2sample-manager."""

from .domain import (
    HACKTRIBE_PROFILE,
    KORG_PROFILE,
    DeviceProfile,
    Library,
    Sample,
    SampleMetadata,
    Slice,
)
from .formats import E2SAllFile, FormatError, RiffChunk, WaveFile

__all__ = [
    "DeviceProfile",
    "E2SAllFile",
    "FormatError",
    "HACKTRIBE_PROFILE",
    "KORG_PROFILE",
    "Library",
    "RiffChunk",
    "Sample",
    "SampleMetadata",
    "Slice",
    "WaveFile",
]
