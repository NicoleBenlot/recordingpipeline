"""CLI Audio Capture & Asset Pipeline.

Modular, object-oriented package for recording, noise filtering,
reviewing, and archiving audio assets with a structured text index.
"""

from .models import RecordingConfig, PipelineResult
from .config import Settings, load_settings, load_env_file
from .index import AudioIndex
from .recorder import AudioRecorder
from .filter import AudioFilter
from .archiver import AudioArchiver
from .pipeline import AudioPipeline

try:
    from .gui import PipelineApp
    _GUI_AVAILABLE = True
except ImportError:  # pragma: no cover - tkinter may be missing
    PipelineApp = None  # type: ignore
    _GUI_AVAILABLE = False

__all__ = [
    "RecordingConfig",
    "PipelineResult",
    "Settings",
    "load_settings",
    "load_env_file",
    "AudioIndex",
    "AudioRecorder",
    "AudioFilter",
    "AudioArchiver",
    "AudioPipeline",
    "PipelineApp",
    "_GUI_AVAILABLE",
]

__version__ = "0.1.0"
