"""Configuration loading for the audio pipeline.

Reads settings from environment variables / a ``.env`` file via
``python-dotenv`` and exposes them through a typed ``Settings`` dataclass.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

logger: logging.Logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv, set_key
    _DOTENV_AVAILABLE = True
except ImportError:  # pragma: no cover - dotenv is optional
    _DOTENV_AVAILABLE = False


# ----------------------------------------------------------------------
def _resolve_project_root() -> Path:
    """Return the project root (parent of this package)."""
    return Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
def env_file_path() -> Path:
    """Return the project ``.env`` path."""
    return _resolve_project_root() / ".env"


# ----------------------------------------------------------------------
def ensure_env_file() -> Optional[Path]:
    """Create ``.env`` from ``.env.example`` when it does not exist yet.

    Keeps a fresh checkout runnable instead of silently falling back to
    built-in defaults, which would archive to ``./recordings`` rather than
    the documented layout.  An existing ``.env`` is never touched.

    Returns
    -------
    Optional[Path]
        The path that was created, or ``None`` if nothing was needed
        (already present, or no ``.env.example`` to copy from).
    """
    path: Path = env_file_path()
    if path.exists():
        return None

    template: Path = path.with_name(".env.example")
    if not template.exists():
        logger.debug("No .env and no .env.example – using built-in defaults.")
        return None

    try:
        path.write_text(
            template.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError as exc:
        logger.error(
            "Could not create %s from %s: %s", path, template.name, exc
        )
        return None
    logger.info("Created %s from %s – review it and adjust.", path, template.name)
    return path


# ----------------------------------------------------------------------
def load_env_file() -> None:
    """Create ``.env`` from the template if needed, then load it."""
    ensure_env_file()
    if not _DOTENV_AVAILABLE:
        return
    load_dotenv(env_file_path())


# ----------------------------------------------------------------------
def set_env_value(key: str, value: str) -> bool:
    """Persist ``key=value`` in the project ``.env``.

    Used by the GUI to remember a toggle between runs.  Comments and
    unrelated keys in the file are preserved, and the value is applied to
    the current process so a later ``load_settings`` agrees with it.

    Parameters
    ----------
    key : str
        Environment variable name, e.g. ``"REDUCE_NOISE"``.
    value : str
        Raw value to store, e.g. ``"0"``.

    Returns
    -------
    bool
        True when the value was written, False if ``.env`` could not be
        updated (the caller should not treat the toggle as remembered).
    """
    path: Path = env_file_path()

    if _DOTENV_AVAILABLE:
        try:
            path.touch(exist_ok=True)
            set_key(str(path), key, value)
            os.environ[key] = value
            return True
        except OSError as exc:
            logger.error("Could not update %s: %s", path, exc)
            return False

    # Fallback used when python-dotenv is not installed.
    try:
        lines: List[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        replaced: bool = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
        if not replaced:
            lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[key] = value
        return True
    except OSError as exc:
        logger.error("Could not update %s: %s", path, exc)
        return False


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
    audio_format : str
        Output container/codec: ``"mp3"``, ``"opus"``, ``"ogg"``,
        ``"wav"``.  ``opus`` needs an ``opusenc`` binary on PATH.
    mp3_bitrate : str
        Target bitrate for compressed formats (e.g. ``"192k"``).
    log_level : str
        Logging level name.
    index_start_number : int
        Number where auto-increment begins (floor for new words when
        extracting/continuing an existing library).
    index_prefix_length : int
        Number of leading letters used to group words into index sections
        (default 2, e.g. ``siya`` → ``[si]``).
    reduce_noise : bool
        Whether noise reduction runs in the background on each capture.
        ``False`` records the raw signal; the GUI's manual denoise
        button and the CLI's ``[f]`` re-process option stay available.
    multi_speaker : bool
        Dupe policy for already-indexed words.  ``True`` keeps every take
        (``amo = 1, 2`` with ``1.mp3`` and ``2.mp3``).  ``False``
        overwrites the selected take (latest by default).
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
    audio_format: str = "mp3"
    mp3_bitrate: str = "192k"
    log_level: str = "INFO"
    index_start_number: int = 1
    index_prefix_length: int = 2
    reduce_noise: bool = True
    multi_speaker: bool = False
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
        audio_format=getenv("AUDIO_FORMAT", "mp3"),
        mp3_bitrate=getenv("MP3_BITRATE", "192k"),
        log_level=getenv("LOG_LEVEL", "INFO"),
        index_start_number=_as_int(
            getenv("INDEX_START_NUMBER", "1"), 1
        ),
        index_prefix_length=_as_int(
            getenv("INDEX_PREFIX_LENGTH", "2"), 2
        ),
        reduce_noise=_as_bool(getenv("REDUCE_NOISE", "1")),
        multi_speaker=_as_bool(getenv("MULTI_SPEAKER", "0")),
    )
