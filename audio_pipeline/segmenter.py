"""Splitting a recorded sentence into one audio cut per word.

Sentence mode has no speech-to-text model: you type the sentence, speak
it, and this module decides where one word ends and the next begins.
It does that from the waveform alone — short-time energy is thresholded
to find the pauses between words, and the resulting cuts are then
reconciled with the number of words you typed (see
:meth:`SentenceSegmenter.split`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger: logging.Logger = logging.getLogger(__name__)

# Punctuation trimmed off a typed token so "amo," and "amo" index alike.
_TRIM_CHARS: str = ".,!?;:'\"()[]{}<>…-–—*"


def split_sentence_words(sentence: str) -> List[str]:
    """Return the lowercased word list of a typed sentence.

    Tokens are split on whitespace, stripped of surrounding punctuation,
    and lowercased to match how :class:`~audio_pipeline.archiver.AudioArchiver`
    normalises words before indexing.

    Parameters
    ----------
    sentence : str
        Raw text from the GUI's sentence field.

    Returns
    -------
    List[str]
        One normalised word per token; empty tokens are dropped.
    """
    words: List[str] = []
    for token in sentence.strip().split():
        cleaned: str = token.strip(_TRIM_CHARS).lower()
        if cleaned:
            words.append(cleaned)
    return words


@dataclass(frozen=True)
class Segment:
    """One cut out of a recording, in sample indices.

    Attributes
    ----------
    start : int
        First sample of the cut (inclusive).
    end : int
        Last sample of the cut (exclusive).
    """

    start: int
    end: int

    @property
    def length(self) -> int:
        """Number of samples in the cut."""
        return self.end - self.start


@dataclass(frozen=True)
class SplitResult:
    """The cuts chosen for a sentence, plus how they were arrived at.

    ``detected`` is how many voiced regions the thresholding found before
    reconciliation, so the GUI can warn when the cut count had to be
    forced to match the typed word count.

    Attributes
    ----------
    segments : List[Segment]
        Exactly one cut per typed word (in order).
    detected : int
        Voiced regions found before reconciliation.
    merged : int
        Regions removed by merging the shortest pauses.
    split : int
        Extra pieces created by cutting long regions in half.
    """

    segments: List[Segment] = field(default_factory=list)
    detected: int = 0
    merged: int = 0
    split: int = 0

    @property
    def adjusted(self) -> bool:
        """True when the cut count had to be forced to the word count."""
        return self.merged > 0 or self.split > 0

    def note(self) -> str:
        """Return a short human-readable summary of any reconciliation."""
        if not self.adjusted:
            return f"{self.detected} pause(s) detected."
        parts: List[str] = []
        if self.merged:
            parts.append(f"merged {self.merged} pause(s)")
        if self.split:
            parts.append(f"split {self.split} long cut(s)")
        return f"{self.detected} pause(s) detected; " + " and ".join(parts) + "."


class SentenceSegmenter:
    """Cuts a recording into one segment per typed word.

    The pipeline is: frame the signal, threshold short-time RMS energy to
    find voiced regions, close pauses too short to be a word break, absorb
    regions too short to be a word, then reconcile the survivors with the
    number of words the user typed.

    Parameters
    ----------
    sample_rate : int
        Capture rate of the audio being split, in Hz.
    frame_ms : float
        Analysis frame length in milliseconds.
    silence_ratio : float
        Energy below ``silence_ratio`` × the loudest analysis frame counts
        as a pause between words.  Lower values cut words apart more
        eagerly.
    noise_multiplier : float
        The gate is also held at this multiple of the estimated noise
        floor, so a quiet recording with a noisy room does not chatter
        across the threshold and shatter one word into several.
    min_word_seconds : float
        Regions shorter than this are absorbed into a neighbour, so a
        click or a breath does not become its own word.
    min_gap_seconds : float
        Pauses shorter than this are ignored, so a stop consonant or a
        plosive inside one word does not split it.
    pad_ms : float
        Silence added either side of each cut so word onsets and offsets
        are not clipped.
    """

    def __init__(
        self,
        sample_rate: int = 44_100,
        frame_ms: float = 20.0,
        silence_ratio: float = 0.14,
        noise_multiplier: float = 3.0,
        min_word_seconds: float = 0.12,
        min_gap_seconds: float = 0.09,
        pad_ms: float = 40.0,
    ) -> None:
        self.sample_rate: int = sample_rate
        self.frame_ms: float = frame_ms
        self.silence_ratio: float = silence_ratio
        self.noise_multiplier: float = noise_multiplier
        self.min_word_seconds: float = min_word_seconds
        self.min_gap_seconds: float = min_gap_seconds
        self.pad_ms: float = pad_ms
        logger.info(
            "SentenceSegmenter initialised  sr=%d  silence_ratio=%.2f  "
            "noise_mult=%.1f  min_word=%.2fs  min_gap=%.2fs  pad=%.0fms",
            sample_rate, silence_ratio, noise_multiplier, min_word_seconds,
            min_gap_seconds, pad_ms,
        )

    # ------------------------------------------------------------------
    def split(
        self,
        audio: np.ndarray,
        word_count: int,
    ) -> SplitResult:
        """Cut *audio* into exactly *word_count* segments.

        Parameters
        ----------
        audio : np.ndarray
            Recorded sentence as float32 samples at ``self.sample_rate``.
        word_count : int
            Number of words that were typed.  When the waveform disagrees
            (a swallowed word, or a phrase read without pauses) the cuts
            are forced to this count: the shortest pauses are merged away
            if there are too many cuts, and the longest cut is halved if
            there are too few.

        Returns
        -------
        SplitResult
            The cuts plus a record of any reconciliation.
        """
        samples: np.ndarray = np.asarray(audio, dtype=np.float32).flatten()
        total: int = samples.size
        if total == 0 or word_count < 1:
            return SplitResult()

        regions: List[Segment] = self._voiced_regions(samples)
        detected: int = len(regions)
        if not regions:
            logger.warning(
                "Nothing above the silence gate – cutting the whole "
                "recording into %d equal piece(s).", word_count,
            )
            regions = [Segment(0, total)]

        regions = self._close_short_gaps(regions)
        regions = self._absorb_short(regions)
        if not regions:
            regions = [Segment(0, total)]
        regions, added = self._reconcile(regions, word_count)
        segments: List[Segment] = [
            self._pad(region, total) for region in regions
        ]
        return SplitResult(
            segments=segments,
            detected=detected,
            merged=max(0, detected - len(segments)),
            split=added,
        )

    # ------------------------------------------------------------------
    def _voiced_regions(self, samples: np.ndarray) -> List[Segment]:
        """Return the contiguous regions whose energy clears the gate."""
        frame: int = max(1, int(self.sample_rate * self.frame_ms / 1000.0))
        if samples.size < frame:
            return [Segment(0, samples.size)]

        frames: int = 1 + (samples.size - frame) // frame
        trimmed: np.ndarray = samples[: frames * frame]
        windows: np.ndarray = trimmed.reshape(frames, frame)
        energy: np.ndarray = np.sqrt(np.mean(windows * windows, axis=1))

        peak: float = float(np.percentile(energy, 95))
        if peak <= 1e-6:
            return []
        floor: float = float(np.percentile(energy, 20))
        threshold: float = max(
            peak * self.silence_ratio, floor * self.noise_multiplier
        )
        if threshold >= peak:
            return []
        voiced: np.ndarray = energy > threshold

        regions: List[Segment] = []
        run_start: Optional[int] = None
        for i, is_voiced in enumerate(voiced):
            if is_voiced and run_start is None:
                run_start = i
            elif not is_voiced and run_start is not None:
                regions.append(self._frame_span(run_start, i + 1, frame, samples.size))
                run_start = None
        if run_start is not None:
            regions.append(
                self._frame_span(run_start, frames, frame, samples.size)
            )
        return regions

    @staticmethod
    def _frame_span(
        first: int, last: int, frame: int, total: int
    ) -> Segment:
        """Convert an inclusive frame range into a clamped sample span."""
        return Segment(
            max(0, first * frame),
            min(total, last * frame),
        )

    # ------------------------------------------------------------------
    def _close_short_gaps(self, regions: List[Segment]) -> List[Segment]:
        """Rejoin regions separated by less than ``min_gap_seconds``.

        A plosive or a stop consonant puts a short dip in the middle of a
        single word; joining those back is what keeps one word in one cut.
        """
        gap: int = self._gap_samples()
        joined: List[Segment] = []
        for region in regions:
            if joined and (region.start - joined[-1].end) < gap:
                joined[-1] = Segment(joined[-1].start, region.end)
            else:
                joined.append(region)
        return joined

    # ------------------------------------------------------------------
    def _absorb_short(self, regions: List[Segment]) -> List[Segment]:
        """Absorb regions too short to be a word into their nearest neighbour."""
        working: List[Segment] = list(regions)
        min_length: int = max(1, int(self.min_word_seconds * self.sample_rate))

        while len(working) > 1:
            shortest: int = min(
                range(len(working)), key=lambda i: working[i].length
            )
            if working[shortest].length >= min_length:
                break
            if shortest == 0:
                neighbour: int = 1
            elif shortest == len(working) - 1:
                neighbour = len(working) - 2
            else:
                before: int = (
                    working[shortest].start - working[shortest - 1].end
                )
                after: int = working[shortest + 1].start - working[shortest].end
                neighbour = shortest - 1 if before <= after else shortest + 1
            low: int = min(shortest, neighbour)
            high: int = max(shortest, neighbour)
            working[low] = Segment(working[low].start, working[high].end)
            working.pop(high)
        return working

    # ------------------------------------------------------------------
    def _reconcile(
        self, regions: List[Segment], word_count: int
    ) -> Tuple[List[Segment], int]:
        """Force the region count to *word_count*.

        Returns the new regions and how many extra pieces were created by
        halving long cuts.
        """
        working: List[Segment] = list(regions)
        added: int = 0

        # Too many cuts: close the shortest pauses until the count fits.
        while len(working) > word_count:
            at: int = min(
                range(len(working) - 1),
                key=lambda i: working[i + 1].start - working[i].end,
            )
            working[at] = Segment(working[at].start, working[at + 1].end)
            working.pop(at + 1)

        # Too few cuts: halve the longest cut until the count fits.
        while len(working) < word_count:
            longest: int = max(
                range(len(working)), key=lambda i: working[i].length
            )
            start: int = working[longest].start
            first, second = _even_split(working[longest].length, 2)
            working[longest : longest + 1] = [
                Segment(start, start + first.end),
                Segment(start + first.end, start + second.end),
            ]
            added += 1

        if added or len(regions) != len(working):
            logger.info(
                "Reconciled %d detected region(s) to %d word(s); %d extra "
                "cut(s) created.", len(regions), word_count, added,
            )
        return working, added

    # ------------------------------------------------------------------
    def _gap_samples(self) -> int:
        """Return ``min_gap_seconds`` in samples."""
        return int(self.min_gap_seconds * self.sample_rate)

    def _pad(self, region: Segment, total: int) -> Segment:
        """Widen *region* by ``pad_ms`` on both sides, clamped to the buffer."""
        pad: int = int(self.pad_ms * self.sample_rate / 1000.0)
        return Segment(
            max(0, region.start - pad),
            min(total, region.end + pad),
        )


def _even_split(length: int, parts: int) -> List[Segment]:
    """Split ``[0, length)`` into *parts* contiguous pieces.

    Piece boundaries are as evenly spaced as possible.
    """
    if parts < 1 or length < 1:
        return []
    size: int = max(1, length // parts)
    pieces: List[Segment] = []
    cursor: int = 0
    for index in range(parts):
        end: int = length if index == parts - 1 else min(length, cursor + size)
        if end > cursor:
            pieces.append(Segment(cursor, end))
        cursor = end
    return pieces
