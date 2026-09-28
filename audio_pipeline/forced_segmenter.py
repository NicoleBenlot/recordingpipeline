"""Sentence splitting by forced alignment, with energy-based fallback.

``SentenceSegmenter`` decides where words end from the waveform alone and
then forces the cut count to match the typed word count.  That works when
the speaker pauses between words, and degrades when they don't.  This
module adds ``ForcedAlignSegmenter``: it uses the typed words themselves,
aligning them to the audio with Meta's MMS CTC aligner
(``torchaudio.pipelines.MMS_FA``, trained on 1,100+ languages, no
pronunciation dictionary needed), so cuts follow where each word is
actually spoken.

It subclasses ``SentenceSegmenter`` and returns the same ``Segment`` /
``SplitResult`` types, so callers keep working unchanged.  Whenever
alignment can't run (torch not installed, a word with no a-z letters,
model error) it logs a warning and falls back to the energy method.

``torch`` / ``torchaudio`` are imported lazily, mirroring ``recorder.py``,
so installs that never use alignment don't need them.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .segmenter import Segment, SentenceSegmenter, SplitResult

logger: logging.Logger = logging.getLogger(__name__)

# MMS_FA's vocabulary is a-z, apostrophe and hyphen.
_OUT_OF_VOCAB = re.compile(r"[^a-z'\-]")

# (bundle, model, tokenizer, aligner) per device, so building a new
# segmenter never reloads the ~1 GB model.
_MODELS: Dict[str, Tuple[Any, Any, Any, Any]] = {}


def normalize_word(word: str) -> str:
    """Reduce one typed word to the aligner's vocabulary.

    Accents are stripped (``ñ`` -> ``n``) and anything else outside
    ``a-z ' -`` is removed *within* the word, so one typed word always
    stays one aligned word.  Returns ``""`` if nothing usable remains.
    """
    folded: str = (
        unicodedata.normalize("NFKD", word)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return _OUT_OF_VOCAB.sub("", folded)


def _load_model(device: Optional[str]) -> Tuple[str, Tuple[Any, Any, Any, Any]]:
    """Load (once) and return ``(device_name, (bundle, model, tok, aligner))``."""
    import torch
    from torchaudio.pipelines import MMS_FA as bundle

    name: str = device or ("cuda" if torch.cuda.is_available() else "cpu")
    if name not in _MODELS:
        logger.info("Loading MMS forced-alignment model on %s …", name)
        model = bundle.get_model().to(torch.device(name))
        _MODELS[name] = (
            bundle, model, bundle.get_tokenizer(), bundle.get_aligner()
        )
    return name, _MODELS[name]


@dataclass(frozen=True)
class AlignedSplitResult(SplitResult):
    """A ``SplitResult`` that also carries per-word alignment detail.

    Attributes
    ----------
    words : List[str]
        The typed words, in order (one per segment).
    scores : List[float]
        Aligner confidence in [0, 1] per word.
    low_confidence : List[str]
        Words scoring under the segmenter's ``min_score``.
    spans : List[Segment]
        Each word's *unpadded* span in samples (``segments`` are padded).
    """

    words: List[str] = field(default_factory=list)
    scores: List[float] = field(default_factory=list)
    low_confidence: List[str] = field(default_factory=list)
    spans: List[Segment] = field(default_factory=list)

    @property
    def adjusted(self) -> bool:
        """True when any word aligned with low confidence."""
        return bool(self.low_confidence)

    def note(self) -> str:
        """Return a short human-readable summary of the alignment."""
        if not self.scores:
            return "Forced alignment."
        worst: int = min(range(len(self.scores)), key=self.scores.__getitem__)
        text: str = (
            f"Forced alignment: {len(self.segments)} word(s), weakest "
            f"'{self.words[worst]}' ({self.scores[worst]:.2f})."
        )
        if self.low_confidence:
            text += (
                " Low confidence: " + ", ".join(self.low_confidence)
                + " — listen before saving."
            )
        return text

    def table(self, sample_rate: int) -> List[Tuple[str, float, float, float]]:
        """Return ``(word, start_s, end_s, score)`` rows in seconds."""
        return [
            (w, round(s.start / sample_rate, 3), round(s.end / sample_rate, 3), sc)
            for w, s, sc in zip(self.words, self.spans, self.scores)
        ]


class ForcedAlignSegmenter(SentenceSegmenter):
    """``SentenceSegmenter`` that prefers forced alignment over pauses.

    Parameters
    ----------
    sample_rate : int
        Capture rate of the audio being split, in Hz.
    use_alignment : bool
        ``False`` makes this behave exactly like ``SentenceSegmenter``.
    device : Optional[str]
        ``"cuda"`` / ``"cpu"``; ``None`` auto-detects.
    min_score : float
        Words aligned below this confidence are reported in
        ``AlignedSplitResult.low_confidence``.
    **energy_kwargs
        Passed to ``SentenceSegmenter`` (``silence_ratio``, ``pad_ms``,
        ...).  ``pad_ms`` also pads aligned cuts; the rest only matter
        for the fallback.
    """

    def __init__(
        self,
        sample_rate: int = 44_100,
        *,
        use_alignment: bool = True,
        device: Optional[str] = None,
        min_score: float = 0.5,
        **energy_kwargs: Any,
    ) -> None:
        super().__init__(sample_rate=sample_rate, **energy_kwargs)
        self.use_alignment: bool = use_alignment
        self.device: Optional[str] = device
        self.min_score: float = min_score

    # ------------------------------------------------------------------
    def split(
        self,
        audio: np.ndarray,
        word_count: int,
        words: Optional[List[str]] = None,
    ) -> SplitResult:
        """Cut *audio* into one segment per word.

        Parameters
        ----------
        audio : np.ndarray
            Recorded sentence, ``(frames,)`` or ``(frames, channels)``.
        word_count : int
            Number of typed words.
        words : Optional[List[str]]
            The typed words (e.g. from ``split_sentence_words``).  Pass
            these to enable alignment; without them (or if their count
            differs from ``word_count``) the energy method is used.

        Returns
        -------
        SplitResult
            An ``AlignedSplitResult`` when alignment ran, otherwise the
            plain energy-based result.
        """
        if not self.use_alignment or not words or len(words) != word_count:
            return super().split(audio, word_count)

        samples: np.ndarray = self._mono(audio)
        if samples.size == 0:
            return SplitResult()

        cleaned: List[str] = [normalize_word(w) for w in words]
        if not all(cleaned):
            bad = [w for w, c in zip(words, cleaned) if not c]
            logger.warning(
                "Can't align %s (no a-z letters) – using energy segmentation.",
                bad,
            )
            return super().split(audio, word_count)

        try:
            aligned = self._align(samples, cleaned)
        except Exception as exc:  # torch missing, download blocked, OOM …
            logger.warning(
                "Forced alignment unavailable (%s) – using energy "
                "segmentation.", exc,
            )
            return super().split(audio, word_count)

        total: int = samples.size
        spans: List[Segment] = []
        scores: List[float] = []
        for start, end, score in aligned:
            start = min(max(0, start), total - 1)
            end = min(max(end, start + 1), total)
            spans.append(Segment(start, end))
            scores.append(round(score, 3))

        return AlignedSplitResult(
            segments=[self._pad(s, total) for s in spans],
            detected=len(spans),
            merged=0,
            split=0,
            words=list(words),
            scores=scores,
            low_confidence=[
                w for w, sc in zip(words, scores) if sc < self.min_score
            ],
            spans=spans,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _mono(audio: np.ndarray) -> np.ndarray:
        """Return contiguous mono float32 samples."""
        arr: np.ndarray = np.asarray(audio, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr.mean(axis=1)
        return np.ascontiguousarray(arr.reshape(-1))

    # ------------------------------------------------------------------
    def _align(
        self, samples: np.ndarray, words: List[str]
    ) -> List[Tuple[int, int, float]]:
        """Align *words* to *samples*; return ``(start, end, score)`` each.

        Sample indices are in the caller's sample rate, not the model's.
        """
        import torch
        import torchaudio

        device, (bundle, model, tokenizer, aligner) = _load_model(self.device)
        wav = torch.from_numpy(samples)[None, :]
        if self.sample_rate != int(bundle.sample_rate):
            wav = torchaudio.functional.resample(
                wav, self.sample_rate, int(bundle.sample_rate)
            )
        with torch.inference_mode():
            emission, _ = model(wav.to(device))
            token_spans = aligner(emission[0], tokenizer(words))

        per_frame: float = samples.size / emission.size(1)
        out: List[Tuple[int, int, float]] = []
        for spans in token_spans:
            score: float = sum(s.score * len(s) for s in spans) / sum(
                len(s) for s in spans
            )
            out.append((
                int(round(spans[0].start * per_frame)),
                int(round(spans[-1].end * per_frame)),
                float(score),
            ))
        return out


def build_segmenter(settings: Any) -> ForcedAlignSegmenter:
    """Build a segmenter from ``Settings``.

    Reads the existing ``segment_*`` fields, plus optional
    ``segment_method`` (``"align"`` default, or ``"energy"``),
    ``alignment_device`` and ``alignment_min_score`` -- these fall back
    to defaults if ``config.py`` doesn't define them yet.
    """
    return ForcedAlignSegmenter(
        sample_rate=settings.sample_rate,
        use_alignment=str(getattr(settings, "segment_method", "align")).lower()
        != "energy",
        device=getattr(settings, "alignment_device", None),
        min_score=getattr(settings, "alignment_min_score", 0.5),
        silence_ratio=settings.segment_silence_ratio,
        noise_multiplier=settings.segment_noise_multiplier,
        min_word_seconds=settings.segment_min_word_seconds,
        min_gap_seconds=settings.segment_min_gap_seconds,
        pad_ms=settings.segment_pad_ms,
    )