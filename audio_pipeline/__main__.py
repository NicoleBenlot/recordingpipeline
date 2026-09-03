"""Entry point: ``python -m audio_pipeline``.

Loads environment/``.env`` configuration, prints available devices,
and runs the orchestrated pipeline with a user-friendly mock setup.
"""

from __future__ import annotations

import sys
import argparse
import logging

from .config import load_settings, Settings
from .models import RecordingConfig
from .recorder import AudioRecorder
from .pipeline import AudioPipeline, _print_result
from . import _GUI_AVAILABLE, PipelineApp


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
    """Run the demo pipeline or launch the GUI.

    Returns
    -------
    int
        Process exit code (0 = success, 1 = failure).
    """
    parser = argparse.ArgumentParser(
        prog="audio_pipeline",
        description="Audio capture & asset pipeline.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the Tkinter GUI instead of the CLI.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Print available audio devices and exit.",
    )
    parser.add_argument(
        "--format",
        default=None,
        metavar="FMT",
        help=(
            "Output audio format: mp3 (default), opus, ogg, wav. "
            "Overrides AUDIO_FORMAT from .env."
        ),
    )
    args = parser.parse_args()

    _ensure_utf8()
    settings: Settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if args.list_devices:
        AudioRecorder.list_devices()
        return 0

    if args.gui:
        if not (_GUI_AVAILABLE and PipelineApp is not None):
            print("Tkinter unavailable – cannot launch GUI.", file=sys.stderr)
            return 1
        app = PipelineApp(settings)
        app.run()
        return 0

    print("╔══════════════════════════════════════════════╗")
    print("║   CLI Audio Capture & Asset Pipeline        ║")
    print("╚══════════════════════════════════════════════╝\n")

    # Show available devices (also lets the user check IDs for INPUT_DEVICE)
    AudioRecorder.list_devices()

    config: RecordingConfig = _build_config(settings)

    print(f"  Word     : {config.word}")
    print(f"  Duration : {config.duration_seconds}s")
    print(f"  Format   : {args.format or settings.audio_format}")
    print(f"  Audio    : {str(settings.resolved_audio_dir)}/")
    print(f"  Index    : {str(settings.index_path)}\n")

    pipeline: AudioPipeline = AudioPipeline(
        config=config,
        audio_dir=str(settings.resolved_audio_dir),
        audio_format=args.format or settings.audio_format,
        input_device=settings.input_device,
        start_number=settings.index_start_number,
        prefix_length=settings.index_prefix_length,
    )
    result = pipeline.run()
    _print_result(result)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
