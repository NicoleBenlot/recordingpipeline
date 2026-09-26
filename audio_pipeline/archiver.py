"""Archival: audio encoding and word→audio text indexing."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from pydub import AudioSegment

from .index import AudioIndex

logger: logging.Logger = logging.getLogger(__name__)


def ensure_ffmpeg_on_path() -> bool:
    """Locate an ``ffmpeg`` executable and add it to ``PATH``.

    Checks the system ``PATH`` first; if missing, scans the WinGet
    ``Gyan.FFmpeg`` install location (common on this machine) and prepends
    its ``bin`` directory to the current process ``PATH``.  Any ffmpeg
    build variant is matched (``full_build``, ``essentials_build``, …),
    not just one.

    Returns
    -------
    bool
        True if ``ffmpeg`` is available after this call.

    Warns
    -----
    If no executable is found, because every save will then fail deep
    inside pydub with an opaque ``FileNotFoundError`` (WinError 2).
    """
    if shutil.which("ffmpeg"):
        return True

    packages_root = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
    )
    if packages_root.exists():
        for pkg in packages_root.glob("Gyan.FFmpeg_*"):
            for build in pkg.glob("ffmpeg-*"):
                bin_dir = build / "bin"
                if (bin_dir / "ffmpeg.exe").exists():
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
                    logger.info("Added ffmpeg bin to PATH: %s", bin_dir)
                    return True

    logger.warning(
        "ffmpeg was not found. Saving audio will fail with "
        "[WinError 2] until it is installed – e.g. "
        "`winget install Gyan.FFmpeg` – or added to PATH. Looked on PATH "
        "and in %s.",
        packages_root,
    )
    return False


# Map a file extension (without dot) to the pydub ``format`` name.
_FORMAT_ALIASES = {
    "mp3": "mp3",
    "opus": "opus",
    "ogg": "ogg",
    "oga": "ogg",
    "wav": "wav",
}


def _normalise_format(audio_format: str) -> str:
    """Return a canonical pydub-ready format for a file extension.

    Parameters
    ----------
    audio_format : str
        File extension / format, e.g. ``"mp3"``, ``"opus"`` or ``".ogg"``.

    Returns
    -------
    str
        The canonical format name used for the filename suffix and the
        pydub ``format`` argument.
    """
    fmt: str = audio_format.strip().lower().lstrip(".")
    return _FORMAT_ALIASES.get(fmt, fmt)


@dataclass(frozen=True)
class SaveTarget:
    """Where one recording of a word should be written.

    Returned by :meth:`AudioArchiver.resolve_target`.  Callers use
    ``number`` as the audio filename stem, save the file, then hand the
    same number to :meth:`AudioArchiver.register_word`.

    Attributes
    ----------
    word : str
        Normalised (lowercased, stripped) word.
    number : int
        The number to write now.
    existing : List[int]
        Numbers already indexed for this word *before* this save, in
        ascending order.  Empty for a word never seen before.
    is_new : bool
        True when the word was not indexed at all.
    is_new_take : bool
        True when an already-indexed word was given a brand-new number
        (multi-speaker mode).  False means ``number`` may already have an
        audio file that this save will replace.
    """

    word: str
    number: int
    existing: List[int] = field(default_factory=list)
    is_new: bool = False
    is_new_take: bool = False

    # ------------------------------------------------------------------
    @property
    def is_dupe(self) -> bool:
        """True when the word already had at least one indexed take."""
        return bool(self.existing)

    # ------------------------------------------------------------------
    @property
    def overwrites(self) -> bool:
        """True when this save replaces an existing audio file."""
        return self.is_dupe and not self.is_new_take

    # ------------------------------------------------------------------
    @property
    def numbers_after(self) -> List[int]:
        """All numbers for the word once this save is registered."""
        return sorted(set(self.existing) | {self.number})


class AudioArchiver:
    """Converts NumPy buffers to compressed audio files and manages storage.

    Audio files are named by an auto-incrementing numeric stem
    (e.g. ``5.mp3`` or ``5.opus``), and each spoken word is recorded in
    the index grouped by its first letters.  A word may hold several
    takes (``amo = 1, 2``), each with its own globally unique number.

    Parameters
    ----------
    output_dir : str
        Target directory for the index file.
    audio_dir : Optional[str]
        Directory for the audio files.  Defaults to ``output_dir``
        when omitted.  Useful for keeping assets in a subfolder while
        the index stays at the destination root.
    index_filename : str
        Name of the structured index text file.
    audio_format : str
        Output container/codec (``"mp3"``, ``"opus"``, ``"ogg"``,
        ``"wav"``, ...).  ``opus`` needs an ``opusenc`` binary on PATH
        (e.g. from ffmpeg).  Default ``"mp3"``.
    bitrate : str
        Target bitrate for compressed formats (default ``"192k"``).
        Ignored for lossless ``wav``.
    sample_rate : int
        Sample rate for the intermediate conversion.
    start_number : int
        Number where auto-increment begins when numbering new words.
    prefix_length : int
        Number of leading letters used to group words into index sections.
    multi_speaker : bool
        Dupe policy.  ``True`` keeps every recording: an already-indexed
        word gets a fresh number and a new audio file.  ``False``
        overwrites, reusing the number of the take chosen by the caller
        (the latest one by default).  Default ``False``, which preserves
        the historical overwrite behaviour.
    """

    def __init__(
        self,
        output_dir: str = "./recordings",
        audio_dir: Optional[str] = None,
        index_filename: str = "audio_index.txt",
        audio_format: str = "mp3",
        bitrate: str = "192k",
        sample_rate: int = 44_100,
        start_number: int = 1,
        prefix_length: int = 2,
        multi_speaker: bool = False,
    ) -> None:
        self.output_dir: Path = Path(output_dir)
        self.audio_dir: Path = Path(audio_dir) if audio_dir else self.output_dir
        self.index_path: Path = self.output_dir / index_filename
        self.audio_format: str = _normalise_format(audio_format)
        self.ext: str = self.audio_format
        self.bitrate: str = bitrate
        self.mp3_bitrate: str = bitrate  # backward-compatible alias
        self.sample_rate: int = sample_rate
        self.start_number: int = start_number
        self.prefix_length: int = prefix_length
        self.multi_speaker: bool = multi_speaker
        ensure_ffmpeg_on_path()
        self._ensure_directory(self.output_dir)
        self._ensure_directory(self.audio_dir)
        logger.info(
            "AudioArchiver initialised  index=%s  audio=%s  start=%d  "
            "multi_speaker=%s",
            self.output_dir,
            self.audio_dir,
            self.start_number,
            self.multi_speaker,
        )

    # ------------------------------------------------------------------
    def numbers_for_word(self, word: str) -> List[int]:
        """Return every number already indexed for *word*.

        Read-only counterpart to :meth:`resolve_target`, for populating
        a take picker without deciding anything.

        Parameters
        ----------
        word : str
            Spoken word (case-insensitive).

        Returns
        -------
        List[int]
            Ascending numbers, empty when the word is not indexed.
        """
        return self._load_index().numbers_for_word(word)

    # ------------------------------------------------------------------
    def clean(self) -> int:
        """Delete all indexed audio files and the index file.

        Removes the audio files recorded so far (based on the index,
        matching this archiver's configured format) and deletes the index
        itself, leaving the directories in place.  Re-recorded words then
        start numbering fresh from ``start_number``.

        Returns
        -------
        int
            Number of audio files removed.
        """
        removed: int = 0
        index: AudioIndex = self._load_index()
        for section in index.words.values():
            for numbers in section.values():
                for number in numbers:
                    audio_file: Path = self.audio_dir / f"{number}.{self.ext}"
                    try:
                        if audio_file.exists():
                            audio_file.unlink()
                            removed += 1
                    except OSError as exc:
                        logger.error("Could not remove %s: %s", audio_file, exc)

        try:
            if self.index_path.exists():
                self.index_path.unlink()
        except OSError as exc:
            logger.error("Could not remove index %s: %s", self.index_path, exc)

        logger.info("Cleaned %d audio file(s) and reset index.", removed)
        return removed

    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_directory(directory: Path) -> None:
        """Create the directory tree if it does not exist.

        Parameters
        ----------
        directory : Path
            Directory to create.
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug("Directory ensured: %s", directory)
        except OSError as exc:
            logger.error("Cannot create output directory: %s", exc)
            raise

    # ------------------------------------------------------------------
    def _load_index(self) -> AudioIndex:
        """Load the on-disk index (or an empty one if missing).

        Returns
        -------
        AudioIndex
            Parsed index using this archiver's ``start_number`` as the
            numbering floor.
        """
        return AudioIndex.from_file(
            self.index_path, self.start_number, self.prefix_length
        )

    # ------------------------------------------------------------------
    def resolve_target(
        self,
        word: str,
        overwrite: Optional[int] = None,
    ) -> SaveTarget:
        """Decide which number the next recording of *word* should use.

        This does not persist anything — it only inspects the index on
        disk.  The caller must save the audio to ``target.number`` and
        then call :meth:`register_word`.

        Parameters
        ----------
        word : str
            Spoken word being recorded (case-insensitive).
        overwrite : Optional[int]
            Which existing take to replace.  Only meaningful when the
            word is already indexed and ``multi_speaker`` is off.  ``None``
            (the default) targets the latest take.  A value the index does
            not hold for this word raises ``ValueError``.

        Returns
        -------
        SaveTarget
            The resolved number plus the context needed to report what
            happened.

        Raises
        ------
        ValueError
            If *word* is empty, or *overwrite* is not one of the word's
            indexed takes.
        """
        normalized: str = word.strip().lower()
        if not normalized:
            raise ValueError("Cannot index an empty word.")

        index: AudioIndex = self._load_index()
        existing: List[int] = index.numbers_for_word(normalized)

        # Never indexed: a plain new word.
        if not existing:
            return SaveTarget(
                word=normalized,
                number=index.next_number,
                existing=[],
                is_new=True,
                is_new_take=False,
            )

        # Multi-speaker: keep every take, allocate a fresh number.
        if self.multi_speaker:
            return SaveTarget(
                word=normalized,
                number=index.next_number,
                existing=existing,
                is_new=False,
                is_new_take=True,
            )

        # Overwrite an existing take, latest unless the caller says otherwise.
        if overwrite is None:
            chosen: int = existing[-1]
        elif overwrite in existing:
            chosen = overwrite
        else:
            raise ValueError(
                f"'{normalized}' has no take {overwrite} "
                f"(indexed takes: {', '.join(str(n) for n in existing)})."
            )
        return SaveTarget(
            word=normalized,
            number=chosen,
            existing=existing,
            is_new=False,
            is_new_take=False,
        )

    # ------------------------------------------------------------------
    def number_for_word(self, word: str) -> int:
        """Return the audio number for *word* without persisting.

        Thin wrapper over :meth:`resolve_target` honouring the current
        ``multi_speaker`` setting.

        Parameters
        ----------
        word : str
            Spoken word being indexed.

        Returns
        -------
        int
            The numeric stem used for the audio filename: a new take when
            ``multi_speaker`` is on, otherwise the word's latest number.
        """
        return self.resolve_target(word).number

    # ------------------------------------------------------------------
    def register_word(self, word: str, number: int) -> List[int]:
        """Attach *number* to *word* and persist the index.

        Idempotent by design, which is what lets the same call serve both
        dupe policies: after a multi-speaker save it appends the new
        number, while after an overwrite save the number is already
        present and the index is left as it was.

        Parameters
        ----------
        word : str
            The spoken word being indexed.
        number : int
            The audio number already assigned to this word.

        Returns
        -------
        List[int]
            Every number now indexed for the word, ascending.

        Raises
        ------
        ValueError
            If *word* is empty.
        """
        index: AudioIndex = self._load_index()
        normalized: str = word.strip().lower()
        numbers: List[int] = index.add_number(normalized, number)
        index.save(self.index_path)
        logger.info("Registered word '%s' → %s", normalized, numbers)
        return numbers

    # ------------------------------------------------------------------
    def save_as(
        self,
        audio: np.ndarray,
        filename: str,
    ) -> str:
        """Encode *audio* to the configured format and write to disk.

        Parameters
        ----------
        audio : np.ndarray
            Float32 sample array (mono or stereo).
        filename : str
            Target filename **without** extension.

        Returns
        -------
        str
            Absolute path to the written audio file.

        Raises
        ------
        RuntimeError
            If encoding fails (e.g. missing ffmpeg / codec binary).
        """
        target_path: Path = self.audio_dir / f"{filename}.{self.ext}"
        wav_path: Path = self.audio_dir / f"{filename}_tmp.wav"

        # Scale float32 [-1,1] to int16 PCM
        pcm: np.ndarray = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32_767).astype(np.int16)

        try:
            # Write temporary WAV
            segment: AudioSegment = AudioSegment(
                data=pcm.tobytes(),
                sample_width=2,  # 16-bit
                frame_rate=self.sample_rate,
                channels=1 if audio.ndim == 1 else audio.shape[1],
            )
            segment.export(str(wav_path), format="wav")

            # Convert WAV to the target format
            audio_seg: AudioSegment = AudioSegment.from_wav(str(wav_path))
            self._export(audio_seg, target_path)

            # Cleanup temporary WAV
            wav_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.error(
                "Audio encoding failed for '%s' (%s): %s",
                filename, self.ext, exc,
            )
            wav_path.unlink(missing_ok=True)
            raise RuntimeError(f"Audio encoding failed: {exc}") from exc

        final: str = str(target_path.resolve())
        logger.info("Saved %s → %s", self.ext, final)
        return final

    # ------------------------------------------------------------------
    def _export(self, segment: AudioSegment, target_path: Path) -> None:
        """Export *segment* to *target_path* using this archiver's format.

        Parameters
        ----------
        segment : AudioSegment
            In-memory audio to encode.
        target_path : Path
            Destination path (its extension implies the format).

        Raises
        ------
        Exception
            Re-raised from ``pydub`` if the codec is unavailable.
        """
        if self.audio_format == "wav":
            segment.export(str(target_path), format="wav")
            return

        segment.export(
            str(target_path),
            format=self.audio_format,
            bitrate=self.bitrate,
        )

    # ------------------------------------------------------------------
    def save_as_mp3(
        self,
        audio: np.ndarray,
        filename: str,
    ) -> str:
        """Backward-compatible alias for :meth:`save_as`.

        Encodes to the currently configured format.  Retained so existing
        callers that reference ``save_as_mp3`` keep working.
        """
        return self.save_as(audio, filename)
