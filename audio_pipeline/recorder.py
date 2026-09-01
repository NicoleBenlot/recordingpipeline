"""Audio capture via sounddevice."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional, Union

import numpy as np
import sounddevice as sd

logger: logging.Logger = logging.getLogger(__name__)


class AudioRecorder:
    """Handles device stream allocation and raw input recording.

    Uses ``sounddevice`` to capture audio from the default (or specified)
    input device and returns a NumPy array of the captured samples.

    Supports both fixed-duration blocking recording (``record``) and
    tap-to-start / tap-to-stop streaming (``start_stream`` /
    ``stop_stream``) for interactive UIs.

    Parameters
    ----------
    sample_rate : int
        Recording sample rate in Hz (default 44 100).
    channels : int
        Number of input channels (1 = mono, 2 = stereo).
    device : Optional[Union[int, str]]
        Sound device identifier; ``None`` selects the system default.
    """

    def __init__(
        self,
        sample_rate: int = 44_100,
        channels: int = 1,
        device: Optional[Union[int, str]] = None,
    ) -> None:
        self.sample_rate: int = sample_rate
        self.channels: int = channels
        self.device: Optional[Union[int, str]] = device
        self._audio_buffer: Optional[np.ndarray] = None
        self._stream: Optional[sd.InputStream] = None
        self._chunks: List[np.ndarray] = []
        self._started_at: float = 0.0
        self._lock: threading.Lock = threading.Lock()
        logger.info(
            "AudioRecorder initialised  sr=%d  ch=%d  device=%s",
            self.sample_rate,
            self.channels,
            self.device or "default",
        )

    # ------------------------------------------------------------------
    @property
    def buffer(self) -> Optional[np.ndarray]:
        """Return the most recently captured audio buffer."""
        return self._audio_buffer

    # ------------------------------------------------------------------
    @property
    def is_streaming(self) -> bool:
        """Return whether a tap-to-record stream is currently active."""
        return self._stream is not None

    # ------------------------------------------------------------------
    @property
    def stream_duration(self) -> float:
        """Elapsed seconds since the active stream started (0 if idle)."""
        if self._stream is None or self._started_at == 0.0:
            return 0.0
        return time.time() - self._started_at

    # ------------------------------------------------------------------
    @staticmethod
    def list_devices() -> None:
        """Print available audio devices to stdout."""
        print("\n--- Available Audio Devices ---")
        print(sd.query_devices())
        print("-------------------------------\n")

    # ------------------------------------------------------------------
    def _stream_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Append an incoming frame to the chunk buffer (stream mode).

        Parameters
        ----------
        indata : np.ndarray
            Incoming audio frame (``frames`` rows by channel count).
        frames : int
            Number of frames in this callback.
        time_info : Any
            PortAudio time info (unused).
        status : sd.CallbackFlags
            Capture status flags (unused).
        """
        if status:
            logger.warning("Stream status: %s", status)
        frame: np.ndarray = indata.copy()
        if self.channels == 1:
            frame = frame.flatten()
        with self._lock:
            self._chunks.append(frame)

    # ------------------------------------------------------------------
    def start_stream(self) -> None:
        """Begin tap-to-record streaming (non-blocking).

        Opens an input stream and starts accumulating samples in the
        background.  Call ``stop_stream`` to finish and get the buffer.

        Raises
        ------
        OSError
            If the audio device cannot be opened.
        sd.PortAudioError
            If the stream cannot be started.
        """
        if self._stream is not None:
            logger.warning("Stream already active – ignoring start.")
            return
        with self._lock:
            self._chunks = []
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=self.device,
                callback=self._stream_callback,
            )
            self._stream.start()
            self._started_at = time.time()
        except (OSError, sd.PortAudioError) as exc:
            self._stream = None
            logger.error("Failed to start stream: %s", exc)
            raise
        logger.info("Recording stream started.")

    # ------------------------------------------------------------------
    def stop_stream(self) -> Optional[np.ndarray]:
        """Stop tap-to-record streaming and return the captured audio.

        Returns
        -------
        Optional[np.ndarray]
            Concatenated float32 samples, or ``None`` if no stream was
            active.
        """
        stream: Optional[sd.InputStream] = self._stream
        if stream is None:
            return None
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            logger.error("Error stopping stream: %s", exc)
        finally:
            self._stream = None
            self._started_at = 0.0

        with self._lock:
            chunks: List[np.ndarray] = self._chunks
            self._chunks = []
        if not chunks:
            logger.warning("No samples captured.")
            return None

        audio: np.ndarray = np.concatenate(chunks).astype(np.float32)
        self._audio_buffer = audio
        logger.info(
            "Stream stopped – shape=%s  dtype=%s",
            audio.shape,
            audio.dtype,
        )
        return audio

    # ------------------------------------------------------------------
    def record(self, duration: float) -> np.ndarray:
        """Record audio for *duration* seconds.

        Parameters
        ----------
        duration : float
            Length of the recording in seconds.

        Returns
        -------
        np.ndarray
            1-D or 2-D array of float32 samples in [-1.0, 1.0].

        Raises
        ------
        OSError
            If the audio device cannot be opened.
        sd.PortAudioError
            If the stream fails during recording.
        """
        logger.info("Recording %.2f s …", duration)
        try:
            raw: np.ndarray = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=self.device,
            )
            sd.wait()  # block until finished
        except (OSError, sd.PortAudioError) as exc:
            logger.error("Recording failed: %s", exc)
            raise

        # Flatten to 1-D for mono
        if self.channels == 1 and raw.ndim > 1:
            raw = raw.flatten()

        self._audio_buffer = raw
        logger.info(
            "Recording complete – shape=%s  dtype=%s",
            raw.shape,
            raw.dtype,
        )
        return raw

    # ------------------------------------------------------------------
    def play(self, audio: np.ndarray) -> None:
        """Playback *audio* through the system speakers (blocking).

        Parameters
        ----------
        audio : np.ndarray
            Float32 sample array.
        """
        logger.info("Playing back audio …")
        try:
            sd.play(audio, samplerate=self.sample_rate, device=self.device)
            sd.wait()
        except (OSError, sd.PortAudioError) as exc:
            logger.error("Playback failed: %s", exc)
            raise
