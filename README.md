# CLI Audio Capture & Asset Pipeline

Modular, object-oriented boilerplate for recording, filtering, reviewing, and
archiving audio assets with a structured text index.

## Features

- **AudioRecorder** — `sounddevice` device stream allocation, raw input capture
  (NumPy float32), and live playback.
- **AudioFilter** — `noisereduce` spectral-gate noise reduction for removing
  structural background hums and hiss.
- **AudioArchiver** — converts processing buffers to highly compressed `.mp3`
  via `pydub`, auto-creates output directories, and appends timestamped JSON
  lines to `audio_index.txt`.
- **AudioPipeline** — orchestrates *Record → Filter → Review → Archive* with an
  interactive confirmation loop (play / re-record / re-filter / save / discard).

## Requirements

- Python 3.9+
- `ffmpeg` (or `libav`) on your PATH for MP3 encoding.

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

Key settings (all optional — sensible defaults are used if omitted):

| Variable | Purpose |
|----------|---------|
| `OUTPUT_DIR` | Base directory for archived assets (`./recordings`) |
| `ARTIFACT_ROOT` | Absolute path override; takes precedence over `OUTPUT_DIR` |
| `AUDIO_SUBDIR` | Subfolder for `.mp3` files (e.g. `audio`); index stays at root |
| `INDEX_FILENAME` | Index file name inside the output dir |
| `DURATION_SECONDS` | Default recording length |
| `INPUT_DEVICE` | Device id (see below); empty = system default |
| `SAMPLE_RATE` / `CHANNELS` | Capture parameters |
| `FILTER_PROP_DECREASE` | Noise-reduction strength first pass |
| `FILTER_AGGRESSIVE_PROP` | Strength for re-filter passes |
| `MAX_FILTER_PASSES` | Max passes before auto-commit |
| `MP3_BITRATE` | Target MP3 bitrate |
| `LOG_LEVEL` | Logging verbosity |

## Usage

```bash
python -m audio_pipeline
```

You'll be prompted for the **word being recorded**, then for a short audio
capture. On first run, list your input devices, then set `INPUT_DEVICE` in
`.env` if the default isn't correct. Devices are always printed at startup.

The `__main__` handler runs a mock capture with an interactive review panel. In
production, wire your own configuration source (e.g. `argparse`, a config file,
or a dashboard) into `AudioPipeline`.

### Interactive review keys

| Key | Action |
|-----|--------|
| `p` | Play current buffer via system speakers |
| `r` | Re-record and restart processing |
| `f` | Re-process with more aggressive filter weights |
| `s` | Commit buffer to archive (numeric-named MP3 + index) |
| `d` | Discard cache and exit |

## Output layout

```
# destination root (ARTIFACT_ROOT / OUTPUT_DIR)
├── audio/               # .mp3 files go here (if AUDIO_SUBDIR=audio)
│   └── 5.mp3            # numeric-named asset, e.g. 5.mp3 for `siya`
└── index.txt            # word→number index (grouped by first letter)
```

## Index format

`index.txt` is an INI-style text file grouping each recorded word by its first
letter and mapping it to the numeric stem of its audio file:

```ini
[a]
ako = 4

[k]
ko = 5

[s]
sa = 9
siya = 5
```

- **`siya = 5`** means the word "siya" is stored as `5.mp3`.
- Numbers auto-increment globally; re-recording the same word reuses its number.
- Words are sorted alphabetically within each letter section.

## Project structure

```
audio_pipeline/         # package
├── __init__.py         # public exports + version
├── __main__.py         # entry point (python -m audio_pipeline)
├── config.py           # Settings + .env loading (python-dotenv)
├── models.py           # RecordingConfig, PipelineResult
├── index.py            # AudioIndex (word→number parse/render)
├── recorder.py         # AudioRecorder (sounddevice)
├── filter.py           # AudioFilter (noisereduce)
├── archiver.py         # AudioArchiver (pydub + index)
└── pipeline.py         # AudioPipeline orchestrator

.env.example            # sample configuration
requirements.txt        # Python dependencies
.gitignore              # ignores artifacts, .env, recordings, index
README.md               # this file
```

## Notes

- The index is grouped by the first letter of each word and sorts entries
  alphabetically; each entry maps a word to its numeric audio file.
- Numbers auto-increment across the whole index; re-recording a word reuses
  its existing number instead of creating a duplicate.
- Tune filtering intensity via `prop_decrease`; the pipeline escalates to a
  configurable aggressive weight on subsequent passes.
