"""Thread-safe, non-blocking sample preview through sounddevice."""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from e2sample_manager.domain.models import Sample

from .errors import ServiceError


class AudioService:
    """Play samples without blocking the UI thread."""

    def __init__(self, *, device: Any = None, blocksize: int = 0) -> None:
        self.device = device
        self.blocksize = blocksize
        self._lock = threading.RLock()
        self._stream: Any = None
        self._audio: np.ndarray | None = None
        self._position = 0
        self._end = 0
        self._loop_start: int | None = None
        self._closed = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._stream is not None and bool(getattr(self._stream, "active", True))

    @staticmethod
    def _frames(sample: Sample) -> np.ndarray:
        channels, _, block_align = sample.wave.pcm_format
        if block_align != channels * 2:
            raise ServiceError("Vorschau unterstützt nur 16-Bit-PCM.")
        data = np.frombuffer(sample.audio_data, dtype="<i2")
        if not len(data) or len(data) % channels:
            raise ServiceError("Das Sample enthält keine gültigen PCM-Frames.")
        # sounddevice's float stream expects normalized samples.
        return np.ascontiguousarray(data.reshape(-1, channels).astype(np.float32) / 32768.0)

    def play(
        self,
        sample: Sample,
        *,
        region: bool = False,
        loop: bool | None = None,
    ) -> None:
        """Start playback; ``region`` applies Electribe Start/Loop/End markers."""

        with self._lock:
            if self._closed:
                raise ServiceError("Der Audio-Service wurde bereits geschlossen.")
        self.stop()
        with self._lock:
            if self._closed:
                raise ServiceError("Der Audio-Service wurde bereits geschlossen.")
            audio = self._frames(sample)
            channels, sample_rate, block_align = sample.wave.pcm_format
            begin = 0
            end = len(audio)
            loop_start: int | None = None
            if region:
                metadata = sample.metadata
                begin = min(metadata.start // block_align, len(audio) - 1)
                end = min((metadata.start + metadata.end) // block_align + 1, len(audio))
                should_loop = not metadata.one_shot if loop is None else loop
                if should_loop:
                    loop_start = min(max(metadata.loop_start // block_align, begin), end - 1)
            elif loop:
                loop_start = 0
            tuned_rate = max(1, round(sample_rate * (2.0 ** (sample.metadata.tune / 12.0))))
            self._audio = audio
            self._position = begin
            self._end = end
            self._loop_start = loop_start
            try:
                import sounddevice as sd

                stream = sd.OutputStream(
                    samplerate=tuned_rate,
                    channels=channels,
                    dtype="float32",
                    device=self.device,
                    blocksize=self.blocksize,
                    callback=self._callback,
                )
                self._stream = stream
                stream.start()
            except Exception as exc:
                self._stream = None
                self._audio = None
                raise ServiceError(
                    "Audiowiedergabe konnte nicht gestartet werden. "
                    f"Ist ein Ausgabegerät verfügbar? ({exc})"
                ) from exc

    def play_full(self, sample: Sample, *, loop: bool = False) -> None:
        self.play(sample, region=False, loop=loop)

    def play_region(self, sample: Sample, *, loop: bool | None = None) -> None:
        self.play(sample, region=True, loop=loop)

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
        import sounddevice as sd

        with self._lock:
            outdata.fill(0)
            if self._audio is None:
                raise sd.CallbackStop
            written = 0
            while written < frames:
                available = self._end - self._position
                if available <= 0:
                    if self._loop_start is None:
                        self._audio = None
                        raise sd.CallbackStop
                    self._position = self._loop_start
                    available = self._end - self._position
                count = min(frames - written, available)
                outdata[written : written + count] = self._audio[
                    self._position : self._position + count
                ]
                written += count
                self._position += count

    def stop(self) -> None:
        with self._lock:
            stream = self._detach_stream_locked()
        self._close_stream(stream)

    def _detach_stream_locked(self) -> Any:
        stream, self._stream = self._stream, None
        self._audio = None
        self._loop_start = None
        return stream

    @staticmethod
    def _close_stream(stream: Any) -> None:
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stream = self._detach_stream_locked()
        self._close_stream(stream)

    def __enter__(self) -> AudioService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
