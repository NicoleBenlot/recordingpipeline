"""Configuration loading for the audio pipeline.

Reads settings from environment variables / a ``.env`` file via
``python-dotenv`` and exposes them through a typed ``Settings`` dataclass.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:  # pragma: no cover - dotenv is optional
    _DOTENV_AVAILABLE = False


def _resolve_project_root() -> Path:
    """Return the project root (parent of this package)."""
    return Path(__file__).resolve().parent.parent


def load_env_file() -> None:
    """Load ``.env`` from the project root, if present."""
    if not _DOTENV_AVAILABLE:
        return
    load_dotenv(_resolve_project_root() / ".env")


@dataclass(frozen=True)
class Settings:
    """Typed view of all pipeline configuration.

    Parameters
    ----------
    output_dir : str
        Base directory for archived assets.
    index_filename : str
        Name of the structured index file inside ``output_dir``.
    artifact_root : Optional[str]
        Optional absolute override for the storage root.
    duration_seconds : float
        Default recording duration in seconds.
    input_device : Optional[str]
        Sound device id (string) or ``None`` for the system default.
    sample_rate : int
        Capture sample rate in Hz.
    channels : int
        Number of input channels.
    filter_prop_decrease : float
        Default noise-reduction strength in [0.0, 1.0].
    filter_aggressive_prop : float
        Aggressive strength used on re-filter passes.
    max_filter_passes : int
        Maximum passes before auto-commit.
    mp3_bitrate : str
        Target MP3 bitrate (e.g. ``"192k"``).
    log_level : str
        Logging level name.
    index_start_number : int
        Number where auto-increment begins (floor for new words when
        extracting/continuing an existing library).
    """

    output_dir: str = "./recordings"
    index_filename: str = "audio_index.txt"
    artifact_root: Optional[str] = None
    audio_subdir: Optional[str] = None
    duration_seconds: float = 5.0
    input_device: Optional[str] = None
    sample_rate: int = 44_100
    channels: int = 1
    filter_prop_decrease: float = 0.75
    filter_aggressive_prop: float = 0.95
    max_filter_passes: int = 2
    mp3_bitrate: str = "192k"
    log_level: str = "INFO"
    index_start_number: int = 1
    _extra: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    @property
    def resolved_destination(self) -> Path:
        """Return the effective storage directory.

        Uses ``artifact_root`` if provided, otherwise ``output_dir``
        resolved relative to the project root.
        """
        if self.artifact_root:
            return Path(self.artifact_root).expanduser().resolve()
        return (Path(self.output_dir)).resolve()

    # ------------------------------------------------------------------
    @property
    def resolved_audio_dir(self) -> Path:
        """Return the directory that holds the ``.mp3`` files.

        Based on the destination root, plus ``audio_subdir`` if set.
        """
        root: Path = self.resolved_destination
        if self.audio_subdir:
            return root / self.audio_subdir
        return root

    # ------------------------------------------------------------------
    @property
    def index_path(self) -> Path:
        """Full path to the index file within the destination directory."""
        return self.resolved_destination / self.index_filename


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_str(value: Optional[str]) -> Optional[str]:
    """Return ``None`` for empty/whitespace string values."""
    if value is None:
        return None
    stripped: str = value.strip()
    return stripped or None


def load_settings(env_file: Optional[str] = None) -> Settings:
    """Build a ``Settings`` instance from the environment.

    Parameters
    ----------
    env_file : Optional[str]
        Path to an explicit ``.env`` file; defaults to project ``.env``.

    Returns
    -------
    Settings
        Populated settings dataclass.
    """
    if env_file:
        if _DOTENV_AVAILABLE:
            load_dotenv(env_file)
    else:
        load_env_file()

    getenv = os.getenv
    return Settings(
        output_dir=getenv("OUTPUT_DIR", "./recordings"),
        index_filename=getenv("INDEX_FILENAME", "audio_index.txt"),
        artifact_root=_as_optional_str(getenv("ARTIFACT_ROOT")),
        audio_subdir=_as_optional_str(getenv("AUDIO_SUBDIR")),
        duration_seconds=_as_float(
            getenv("DURATION_SECONDS", "5.0"), 5.0
        ),
        input_device=_as_optional_str(getenv("INPUT_DEVICE")),
        sample_rate=_as_int(getenv("SAMPLE_RATE", "44100"), 44_100),
        channels=_as_int(getenv("CHANNELS", "1"), 1),
        filter_prop_decrease=_as_float(
            getenv("FILTER_PROP_DECREASE", "0.75"), 0.75
        ),
        filter_aggressive_prop=_as_float(
            getenv("FILTER_AGGRESSIVE_PROP", "0.95"), 0.95
        ),
        max_filter_passes=_as_int(
            getenv("MAX_FILTER_PASSES", "2"), 2
        ),
        mp3_bitrate=getenv("MP3_BITRATE", "192k"),
        log_level=getenv("LOG_LEVEL", "INFO"),
        index_start_number=_as_int(
            getenv("INDEX_START_NUMBER", "1"), 1
        ),
    )
