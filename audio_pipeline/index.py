"""Parsing and rendering of the word→audio index.

The index is a human-readable, INI-style text file grouped by the first
letter of each word::

    [a]
    ako = 3
    [k]
    ko = 8

Each entry maps a spoken word to the numeric stem of its audio file
(e.g. ``ako = 3`` means ``ako.mp3`` aliases ``3.mp3``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger: logging.Logger = logging.getLogger(__name__)

# Matches a section header like "[s]"
_SECTION_RE = re.compile(r"^\[(?P<key>[a-zA-Z0-9])\]\s*$")
# Matches an entry like "ako = 3"
_ENTRY_RE = re.compile(r"^\s*(?P<word>.+?)\s*=\s*(?P<number>\d+)\s*$")


class AudioIndex:
    """An in-memory model of the grouped word→audio-number index.

    Words are grouped by their first character (lowercased).  Numbers are
    tracked globally so new words auto-increment.

    Parameters
    ----------
    words : Dict[str, Dict[str, int]]
        Mapping of ``section -> {word: number}``.  Built by loading an
        existing index file, or empty for a fresh index.
    """

    def __init__(self, words: Optional[Dict[str, Dict[str, int]]] = None) -> None:
        self.words: Dict[str, Dict[str, int]] = words or {}

    # ------------------------------------------------------------------
    @property
    def next_number(self) -> int:
        """Return the next auto-increment number (max existing + 1)."""
        highest: int = 0
        for section in self.words.values():
            for number in section.values():
                if number > highest:
                    highest = number
        return highest + 1

    # ------------------------------------------------------------------
    def add_word(self, word: str) -> int:
        """Register *word* and return its audio number.

        Parameters
        ----------
        word : str
            Spoken word to index (case-insensitive, leading letter used
            for the section).

        Returns
        -------
        int
            The number used as the audio filename stem.
        """
        normalized: str = word.strip().lower()
        if not normalized:
            raise ValueError("Cannot index an empty word.")

        section_key: str = normalized[0]
        section: Dict[str, int] = self.words.setdefault(section_key, {})

        # If the word was already indexed, reuse its number.
        if normalized in section:
            return section[normalized]

        number: int = self.next_number
        section[normalized] = number
        return number

    # ------------------------------------------------------------------
    def render(self) -> str:
        """Render the index to its INI-style text representation.

        Returns
        -------
        str
            Formatted index text with sections and sorted entries.
        """
        lines: List[str] = []
        for key in sorted(self.words):
            lines.append(f"[{key}]")
            section: Dict[str, int] = self.words[key]
            # Sort alphabetically by word
            for word in sorted(section):
                lines.append(f"{word} = {section[word]}")
            lines.append("")
        # Drop trailing blank line
        if lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path) -> "AudioIndex":
        """Parse an existing index file into an ``AudioIndex``.

        Parameters
        ----------
        path : Path
            Path to the index text file.

        Returns
        -------
        AudioIndex
            Parsed model.  An empty model if the file is missing.
        """
        words: Dict[str, Dict[str, int]] = {}
        if not path.exists():
            return cls(words)

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
                        number: int = int(entry_match.group("number"))
                        words[current_section][word] = number
        except OSError as exc:
            logger.error("Failed to read index %s: %s", path, exc)
            raise

        return cls(words)

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
