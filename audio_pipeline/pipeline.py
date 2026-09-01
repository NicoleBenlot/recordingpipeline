"""Pipeline orchestrator: Record → Filter → Review → Archive."""

from __future__ import annotations

import sys
import json
import logging
from dataclasses import asdict
from typing import Optional, Tuple, Union

logger: logging.Logger = logging.getLogger(__name__)

import numpy as np

from .models import PipelineResult, RecordingConfig
from .recorder import AudioRecorder
from .filter import AudioFilter
from .archiver import AudioArchiver


class AudioPipeline:
    """Orchestrates Record → Filter → Review → Archive.

    Parameters
    ----------
    config : RecordingConfig
        Session configuration.
    filter_passes : int
        Maximum number of noise-reduction passes before auto-commit.
    aggressive_prop : float
        ``prop_decrease`` used for the aggressive second pass.
    audio_dir : Optional[str]
        Directory for ``.mp3`` files; defaults to the output dir.
    input_device : Optional[Union[int, str]]
        Sound device for capture; ``None`` uses the default.
    start_number : int
        Floor for auto-incrementing new word numbers.
    prefix_length : int
        Number of leading letters used to group index sections.
    """

    def __init__(
        self,
        config: RecordingConfig,
        filter_passes: int = 2,
        aggressive_prop: float = 0.95,
        audio_dir: Optional[str] = None,
        input_device: Optional[Union[int, str]] = None,
        start_number: int = 1,
        prefix_length: int = 2,
    ) -> None:
        self.config: RecordingConfig = config
        self.max_passes: int = filter_passes
        self.aggressive_prop: float = aggressive_prop

        self.recorder: AudioRecorder = AudioRecorder(
            sample_rate=config.sample_rate,
            channels=config.channels,
            device=input_device,
        )
        self.filter: AudioFilter = AudioFilter(
            sample_rate=config.sample_rate,
        )
        self.archiver: AudioArchiver = AudioArchiver(
            output_dir=config.output_dir,
            audio_dir=audio_dir,
            sample_rate=config.sample_rate,
            start_number=start_number,
            prefix_length=prefix_length,
        )

    # ------------------------------------------------------------------
    def _review_loop(self, audio: np.ndarray) -> Tuple[np.ndarray, int]:
        """Interactive listen / re-process / commit / discard loop.

        Returns
        -------
        Tuple[np.ndarray, int]
            The accepted audio buffer and the number of filter passes
            applied.  A pass count of ``-1`` signals a discard.
        """
        passes: int = 1
        current: np.ndarray = audio

        while True:
            print("\n╔══════════════════════════════════════╗")
            print("║        AUDIO REVIEW PANEL            ║")
            print("╚══════════════════════════════════════╝")
            print("  [p] Play audio")
            print("  [r] Re-record (restart)")
            print("  [f] Re-process with more aggressive filter")
            print("  [s] Save / commit current buffer")
            print("  [d] Discard and exit")
            print(f"\n  Current filter passes: {passes}")
            choice: str = input("  ➤ Choose an action: ").strip().lower()

            if choice == "p":
                try:
                    self.recorder.play(current)
                except Exception as exc:
                    print(f"  ⚠ Playback error: {exc}", file=sys.stderr)

            elif choice == "r":
                print("  ↻ Restarting recording …")
                current = self.recorder.record(self.config.duration_seconds)
                passes = 1
                self.filter.prop_decrease = 0.75
                current = self.filter.reduce_noise(current)
                passes = 1

            elif choice == "f":
                if passes >= self.max_passes:
                    print(
                        f"  ⚠ Maximum filter passes ({self.max_passes}) "
                        "reached.",
                        file=sys.stderr,
                    )
                    continue
                self.filter.prop_decrease = self.aggressive_prop
                current = self.filter.reduce_noise(current)
                passes += 1
                print(f"  ✓ Aggressive filter applied (pass {passes}).")

            elif choice == "s":
                print("  ✓ Committing buffer to archive …")
                return current, passes

            elif choice == "d":
                print("  ✗ Buffer discarded.")
                return current, -1  # sentinel

            else:
                print("  ⚠ Invalid choice.  Try again.")

        # Unreachable but satisfies type-checkers
        return current, passes  # pragma: no cover

    # ------------------------------------------------------------------
    def run(self) -> PipelineResult:
        """Execute the full pipeline.

        Returns
        -------
        PipelineResult
            Summary of the session outcome.
        """
        result: PipelineResult = PipelineResult(success=False)
        result.word = self.config.word

        # ---- Step 1: Record ------------------------------------------------
        try:
            raw: np.ndarray = self.recorder.record(
                self.config.duration_seconds
            )
            result.duration_recorded = self.config.duration_seconds
        except Exception as exc:
            msg = f"Recording failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        # ---- Step 2: Initial noise reduction --------------------------------
        filtered: np.ndarray = self.filter.reduce_noise(raw)
        result.filter_passes = 1

        # ---- Step 3: Review loop -------------------------------------------
        accepted, passes = self._review_loop(filtered)

        if passes < 0:
            result.errors.append("User discarded the recording.")
            return result

        result.filter_passes = passes

        # ---- Step 4: Assign number + archive --------------------------------
        try:
            number: int = self.archiver.number_for_word(self.config.word)
        except Exception as exc:
            msg = f"Index lookup failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        result.number = number
        try:
            filepath: str = self.archiver.save_as_mp3(accepted, str(number))
        except Exception as exc:
            msg = f"Archive failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        # ---- Step 5: Persist index ------------------------------------------
        try:
            self.archiver.register_word(self.config.word, number)
        except Exception as exc:
            msg = f"Index write failed: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        result.success = True
        result.filepath = filepath
        result.notes = self.config.notes
        logger.info(
            "Pipeline finished: '%s' → %d.mp3 → %s",
            self.config.word,
            number,
            filepath,
        )
        return result


def _print_result(result: PipelineResult) -> None:
    """Render a ``PipelineResult`` as a formatted summary."""
    print("\n--- Pipeline Result ---")
    print(json.dumps(asdict(result), indent=2, default=str))
