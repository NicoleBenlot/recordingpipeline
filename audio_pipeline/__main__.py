"""Entry point: ``python -m audio_pipeline``.

Loads environment/``.env`` configuration, prints available devices,
and runs the orchestrated pipeline with a user-friendly mock setup.
"""

from __future__ import annotations

import sys
import logging

from .config import load_settings, Settings
from .models import RecordingConfig
from .recorder import AudioRecorder
from .pipeline import AudioPipeline, _print_result


def _ensure_utf8() -> None:
    """Force UTF-8 I/O so Unicode glyphs render on Windows consoles."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # non-text or pre-3.7 streams


def _build_config(settings: Settings) -> RecordingConfig:
    """Build a ``RecordingConfig`` from loaded settings.

    Parameters
    ----------
    settings : Settings
        Typed pipeline settings.

    Returns
    -------
    RecordingConfig
        Session configuration for a single capture.
    """
    word: str = input("  ➤ Word to record: ").strip()
    return RecordingConfig(
        duration_seconds=settings.duration_seconds,
        sample_rate=settings.sample_rate,
        channels=settings.channels,
        output_dir=str(settings.resolved_destination),
        notes="",
        word=word,
    )


def main() -> int:
    """Run the demo pipeline.

    Returns
    -------
    int
        Process exit code (0 = success, 1 = failure).
    """
    _ensure_utf8()
    settings: Settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    print("╔══════════════════════════════════════════════╗")
    print("║   CLI Audio Capture & Asset Pipeline        ║")
    print("╚══════════════════════════════════════════════╝\n")

    # Show available devices (also lets the user check IDs for INPUT_DEVICE)
    AudioRecorder.list_devices()

    config: RecordingConfig = _build_config(settings)

    print(f"  Word     : {config.word}")
    print(f"  Duration : {config.duration_seconds}s")
    print(f"  Audio    : {str(settings.resolved_audio_dir)}/")
    print(f"  Index    : {str(settings.index_path)}\n")

    pipeline: AudioPipeline = AudioPipeline(
        config=config,
        audio_dir=str(settings.resolved_audio_dir),
        input_device=settings.input_device,
    )
    result = pipeline.run()
    _print_result(result)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
