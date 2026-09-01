"""Archival: MP3 encoding and word→audio text indexing."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
from pydub import AudioSegment

from .index import AudioIndex

logger: logging.Logger = logging.getLogger(__name__)


def ensure_ffmpeg_on_path() -> bool:
    """Locate an ``ffmpeg`` executable and add it to ``PATH``.

    Checks the system ``PATH`` first; if missing, scans the WinGet
    ``Gyan.FFmpeg`` install location (common on this machine) and prepends
    its ``bin`` directory to the current process ``PATH``.

    Returns
    -------
    bool
        True if ``ffmpeg`` is available after this call.
    """
    if shutil.which("ffmpeg"):
        return True

    packages_root = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
    )
    if not packages_root.exists():
        return False

    for pkg in packages_root.glob("Gyan.FFmpeg_*"):
        for build in pkg.glob("ffmpeg-*-full_build"):
            bin_dir = build / "bin"
            if (bin_dir / "ffmpeg.exe").exists():
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
                logger.info("Added ffmpeg bin to PATH: %s", bin_dir)
                return True
    return False


class AudioArchiver:
    """Converts NumPy buffers to compressed ``.mp3`` and manages storage.

    Audio files are named by an auto-incrementing numeric stem
    (e.g. ``5.mp3``), and each spoken word is recorded in ``index.txt``
    grouped by its first letter.

    Parameters
    ----------
    output_dir : str
        Target directory for the index file.
    audio_dir : Optional[str]
        Directory for the ``.mp3`` files.  Defaults to ``output_dir``
        when omitted.  Useful for keeping assets in a subfolder while
        the index stays at the destination root.
    index_filename : str
        Name of the structured index text file.
    mp3_bitrate : str
        Target MP3 bitrate (default ``"192k"``).
    sample_rate : int
        Sample rate for WAV intermediate conversion.
    start_number : int
        Number where auto-increment begins when numbering new words.
    """

    def __init__(
        self,
        output_dir: str = "./recordings",
        audio_dir: Optional[str] = None,
        index_filename: str = "audio_index.txt",
        mp3_bitrate: str = "192k",
        sample_rate: int = 44_100,
        start_number: int = 1,
    ) -> None:
        self.output_dir: Path = Path(output_dir)
        self.audio_dir: Path = Path(audio_dir) if audio_dir else self.output_dir
        self.index_path: Path = self.output_dir / index_filename
        self.mp3_bitrate: str = mp3_bitrate
        self.sample_rate: int = sample_rate
        self.start_number: int = start_number
        ensure_ffmpeg_on_path()
        self._ensure_directory(self.output_dir)
        self._ensure_directory(self.audio_dir)
        logger.info(
            "AudioArchiver initialised  index=%s  audio=%s  start=%d",
            self.output_dir,
            self.audio_dir,
            self.start_number,
        )

    # ------------------------------------------------------------------
    def clean(self) -> int:
        """Delete all indexed audio files and the index file.

        Removes the ``.mp3`` files recorded so far (based on the index)
        and deletes the index itself, leaving the directories in place.
        Re-recorded words then start numbering fresh from
        ``start_number``.

        Returns
        -------
        int
            Number of ``.mp3`` files removed.
        """
        removed: int = 0
        index: AudioIndex = self._load_index()
        for section in index.words.values():
            for number in section.values():
                audio_file: Path = self.audio_dir / f"{number}.mp3"
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
        return AudioIndex.from_file(self.index_path, self.start_number)

    # ------------------------------------------------------------------
    def number_for_word(self, word: str) -> int:
        """Return the stable audio number for *word* without persisting.

        Parameters
        ----------
        word : str
            Spoken word being indexed.

        Returns
        -------
        int
            The numeric stem used for the audio filename, reusing an
            existing number if the word was already recorded.
        """
        index: AudioIndex = self._load_index()
        return index.add_word(word)

    # ------------------------------------------------------------------
    def register_word(self, word: str, number: int) -> None:
        """Record *word* mapped to *number* and persist the index.

        Parameters
        ----------
        word : str
            The spoken word being indexed.
        number : int
            The audio number already assigned to this word.
        """
        index: AudioIndex = self._load_index()
        index.words.setdefault(word.strip().lower()[0], {})[
            word.strip().lower()
        ] = number
        index.save(self.index_path)
        logger.info("Registered word '%s' → %d", word.strip().lower(), number)

    # ------------------------------------------------------------------
    def save_as_mp3(
        self,
        audio: np.ndarray,
        filename: str,
    ) -> str:
        """Encode *audio* to MP3 and write to disk.

        Parameters
        ----------
        audio : np.ndarray
            Float32 sample array (mono or stereo).
        filename : str
            Target filename **without** extension.

        Returns
        -------
        str
            Absolute path to the written ``.mp3`` file.

        Raises
        ------
        RuntimeError
            If encoding fails (e.g. missing ffmpeg).
        """
        mp3_path: Path = self.audio_dir / f"{filename}.mp3"
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

            # Convert WAV → MP3
            audio_seg: AudioSegment = AudioSegment.from_wav(str(wav_path))
            audio_seg.export(
                str(mp3_path), format="mp3", bitrate=self.mp3_bitrate
            )

            # Cleanup temporary WAV
            wav_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.error("MP3 encoding failed for '%s': %s", filename, exc)
            wav_path.unlink(missing_ok=True)
            raise RuntimeError(f"MP3 encoding failed: {exc}") from exc

        final: str = str(mp3_path.resolve())
        logger.info("Saved MP3 → %s", final)
        return final
