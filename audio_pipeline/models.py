"""Data models used across the audio pipeline."""

from __future__ import annotations

import uuid
import datetime
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class RecordingConfig:
    """Immutable configuration for a single capture session.

    Parameters
    ----------
    duration_seconds : float
        Length of the recording in seconds.
    sample_rate : int
        Sample rate in Hz (default 44 100).
    channels : int
        Number of input channels.
    output_dir : str
        Target directory for archived files.
    filename : str
        Base filename (without extension).  Auto-generates if empty.
    notes : str
        User-provided metadata notes.
    """

    duration_seconds: float
    sample_rate: int = 44_100
    channels: int = 1
    output_dir: str = "./recordings"
    filename: str = ""
    notes: str = ""
    word: str = ""

    # ------------------------------------------------------------------
    def resolved_filename(self) -> str:
        """Return the user filename or a UUID-based default."""
        return self.filename or f"capture_{uuid.uuid4().hex[:12]}"


@dataclass
class PipelineResult:
    """Captures the outcome of a single pipeline run.

    Parameters
    ----------
    success : bool
        Whether the run completed and committed an asset.
    filepath : Optional[str]
        Absolute path to the written MP3, if any.
    duration_recorded : float
        Seconds of audio actually captured.
    filter_passes : int
        Number of noise-reduction passes applied.
    notes : str
        Notes attached to this run.
    errors : List[str]
        Human-readable error messages encountered.
    """

    success: bool
    filepath: Optional[str] = None
    word: str = ""
    number: Optional[int] = None
    duration_recorded: float = 0.0
    filter_passes: int = 0
    notes: str = ""
    errors: List[str] = field(default_factory=list)


def timestamped_filename(prefix: str = "capture") -> str:
    """Return a timestamped base filename like ``capture_20260901_153000``."""
    return f"{prefix}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
