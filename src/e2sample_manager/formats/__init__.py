"""Public binary-format API."""

from .e2s_all import HEADER_SIZE, MAGIC, POINTER_COUNT, E2SAllFile
from .riff import EsliCodec, FormatError, RiffChunk, WaveFile

__all__ = [
    "E2SAllFile",
    "EsliCodec",
    "FormatError",
    "HEADER_SIZE",
    "MAGIC",
    "POINTER_COUNT",
    "RiffChunk",
    "WaveFile",
]
