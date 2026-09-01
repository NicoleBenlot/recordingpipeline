"""Audio capture via sounddevice."""

from __future__ import annotations

import logging
from typing import Optional, Union

import numpy as np
import sounddevice as sd

logger: logging.Logger = logging.getLogger(__name__)


class AudioRecorder:
    """Handles device stream allocation and raw input recording.

    Uses ``sounddevice`` to capture audio from the default (or specified)
    input device and returns a NumPy array of the captured samples.

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
    @staticmethod
    def list_devices() -> None:
        """Print available audio devices to stdout."""
        print("\n--- Available Audio Devices ---")
        print(sd.query_devices())
        print("-------------------------------\n")

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
