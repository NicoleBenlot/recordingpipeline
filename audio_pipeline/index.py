"""Parsing and rendering of the word→audio index.

The index is a human-readable, INI-style text file grouped by the first
few letters (prefix) of each word::

    [ak]
    ako = 3
    [ko]
    ko = 8, 12

Each entry maps a spoken word to one or more numeric stems of its audio
files (e.g. ``ako = 3`` means ``ako`` is stored as ``3.mp3``, while
``ko = 8, 12`` means ``ko`` has two takes, ``8.mp3`` and ``12.mp3`` —
typically one per speaker).  The prefix length is configurable (default
2 letters).  Single-number entries written by earlier versions parse
unchanged, so no migration is needed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger: logging.Logger = logging.getLogger(__name__)

# Matches a section header like "[ak]" (1 or 2 letters/numbers)
_SECTION_RE = re.compile(r"^\[(?P<key>[a-zA-Z0-9]{1,2})\]\s*$")
# Matches an entry like "ako = 3" or "ko = 8, 12"
_ENTRY_RE = re.compile(
    r"^\s*(?P<word>.+?)\s*=\s*(?P<numbers>\d+(?:\s*,\s*\d+)*)\s*$"
)


class AudioIndex:
    """An in-memory model of the grouped word→audio-number index.

    Words are grouped by a lowercase prefix (default 2 letters, e.g.
    ``siya`` → ``[si]``).  Each word maps to a *list* of numbers so the
    same word can have several takes (e.g. one per speaker).  Numbers are
    tracked globally so new words and new takes auto-increment.

    Parameters
    ----------
    words : Optional[Dict[str, Dict[str, List[int]]]]
        Mapping of ``section -> {word: [numbers]}``.  Built by loading an
        existing index file, or empty for a fresh index.
    start_number : int
        Floor for auto-increment when numbering new words or takes.
    prefix_length : int
        Number of leading letters used for the grouping key.
    """

    def __init__(
        self,
        words: Optional[Dict[str, Dict[str, List[int]]]] = None,
        start_number: int = 1,
        prefix_length: int = 2,
    ) -> None:
        self.words: Dict[str, Dict[str, List[int]]] = words or {}
        self.start_number: int = start_number
        self.prefix_length: int = prefix_length

    # ------------------------------------------------------------------
    @property
    def next_number(self) -> int:
        """Return the next auto-increment number.

        Starts at ``start_number`` (or higher if existing entries exceed
        it) and advances by one from the highest number in use across
        every take of every word.
        """
        highest: int = self.start_number - 1
        for section in self.words.values():
            for numbers in section.values():
                for number in numbers:
                    if number > highest:
                        highest = number
        return highest + 1

    # ------------------------------------------------------------------
    def _key(self, word: str) -> str:
        """Return the section key for *word* without mutating the index."""
        return word.strip().lower()[: self.prefix_length]

    # ------------------------------------------------------------------
    def numbers_for_word(self, word: str) -> List[int]:
        """Return the numbers already indexed for *word*.

        Parameters
        ----------
        word : str
            Spoken word to look up (case-insensitive).

        Returns
        -------
        List[int]
            A copy of the word's numbers in ascending order, or an empty
            list when the word has never been indexed.
        """
        normalized: str = word.strip().lower()
        if not normalized:
            return []
        section: Dict[str, List[int]] = self.words.get(
            self._key(normalized), {}
        )
        return sorted(section.get(normalized, []))

    # ------------------------------------------------------------------
    def add_number(self, word: str, number: int) -> List[int]:
        """Ensure *number* is one of *word*'s takes and return the list.

        Idempotent: re-adding a number already present leaves the word
        untouched, which is what makes re-recording with an existing
        number a no-op at the index level.

        Parameters
        ----------
        word : str
            Spoken word (case-insensitive).
        number : int
            Audio number to attach to the word.

        Returns
        -------
        List[int]
            The word's numbers after the update, in ascending order.
        """
        normalized: str = word.strip().lower()
        if not normalized:
            raise ValueError("Cannot index an empty word.")
        section: Dict[str, List[int]] = self.words.setdefault(
            self._key(normalized), {}
        )
        numbers: List[int] = sorted(set(section.get(normalized, [])) | {number})
        section[normalized] = numbers
        return numbers

    # ------------------------------------------------------------------
    def add_word(self, word: str) -> int:
        """Register *word* and return the number to write.

        This is the *overwrite* path: an already-indexed word reuses its
        latest number, so the audio file at that stem is replaced.

        Parameters
        ----------
        word : str
            Spoken word to index (case-insensitive; the leading
            ``prefix_length`` characters form the section key).

        Returns
        -------
        int
            The number used as the audio filename stem.
        """
        normalized: str = word.strip().lower()
        if not normalized:
            raise ValueError("Cannot index an empty word.")

        existing: List[int] = self.numbers_for_word(normalized)
        if existing:
            return existing[-1]

        number: int = self.next_number
        self.add_number(normalized, number)
        return number

    # ------------------------------------------------------------------
    def render(self) -> str:
        """Render the index to its INI-style text representation.

        Returns
        -------
        str
            Formatted index text with sections and sorted entries.  Words
            with several takes render as a comma-separated list
            (``ko = 8, 12``); single-take words render unchanged.
        """
        lines: List[str] = []
        for key in sorted(self.words):
            lines.append(f"[{key}]")
            section: Dict[str, List[int]] = self.words[key]
            # Sort alphabetically by word
            for word in sorted(section):
                numbers: str = ", ".join(str(n) for n in section[word])
                lines.append(f"{word} = {numbers}")
            lines.append("")
        # Drop trailing blank line
        if lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    @classmethod
    def from_file(
        cls, path: Path, start_number: int = 1, prefix_length: int = 2
    ) -> "AudioIndex":
        """Parse an existing index file into an ``AudioIndex``.

        Parameters
        ----------
        path : Path
            Path to the index text file.
        start_number : int
            Floor for auto-increment when numbering new words or takes.
        prefix_length : int
            Number of leading letters used for the grouping key.

        Returns
        -------
        AudioIndex
            Parsed model.  An empty model if the file is missing.
        """
        words: Dict[str, Dict[str, List[int]]] = {}
        if not path.exists():
            return cls(words, start_number, prefix_length)

        current_section: Optional[str] = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line: str = raw_line.strip()
                    if not line:
                        continue

                    section_match = _SECTION_RE.match(line)
                    if section_match:
                        current_section = section_match.group("key").lower()
                        words.setdefault(current_section, {})
                        continue

                    entry_match = _ENTRY_RE.match(line)
                    if entry_match and current_section is not None:
                        word: str = entry_match.group("word").strip().lower()
                        numbers: List[int] = [
                            int(part)
                            for part in entry_match.group("numbers").split(",")
                        ]
                        existing: List[int] = words[current_section].get(word, [])
                        words[current_section][word] = sorted(
                            set(existing) | set(numbers)
                        )
        except OSError as exc:
            logger.error("Failed to read index %s: %s", path, exc)
            raise

        return cls(words, start_number, prefix_length)

    # ------------------------------------------------------------------
    def save(self, path: Path) -> None:
        """Write the index model back to disk.

        Parameters
        ----------
        path : Path
            Destination file path.

        Raises
        ------
        OSError
            If the file cannot be written.
        """
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.render())
            logger.info("Index written → %s", path)
        except OSError as exc:
            logger.error("Failed to write index %s: %s", path, exc)
            raise
