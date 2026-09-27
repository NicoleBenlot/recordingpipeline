# CLI Audio Capture & Asset Pipeline

Modular, object-oriented for recording, filtering, reviewing, and
archiving audio assets with a structured text index.

## Features

- **AudioRecorder** — `sounddevice` device stream allocation, raw input capture
  (NumPy float32), and live playback.
- **AudioFilter** — `noisereduce` spectral-gate noise reduction for removing
  structural background hums and hiss.
- **SentenceSegmenter** — cuts a recorded sentence into one audio clip per
  word by finding the pauses in the waveform (numpy only, no speech model).
- **AudioArchiver** — converts processing buffers to highly compressed `.mp3`
  via `pydub`, auto-creates output directories, and writes the structured
  `audio_index.txt`.
- **AudioPipeline** — orchestrates *Record → Filter → Review → Archive* with an
  interactive confirmation loop (play / re-record / re-filter / save / discard).
- **PipelineApp (GUI)** — Tkinter interface with tap-to-record / tap-again-to-stop,
  adjustable length, playback, denoise, and save/discard (\`python -m audio_pipeline --gui\`).

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
| `SEGMENT_SILENCE_RATIO` | Sentence mode: energy below this fraction of the loudest frame is a pause |
| `SEGMENT_NOISE_MULTIPLIER` | Sentence mode: gate floor as a multiple of the noise floor |
| `SEGMENT_MIN_WORD_SECONDS` | Sentence mode: shorter regions are absorbed into a neighbour |
| `SEGMENT_MIN_GAP_SECONDS` | Sentence mode: pauses shorter than this do not split a word |
| `SEGMENT_PAD_MS` | Sentence mode: silence kept either side of each word cut |
| `LOG_LEVEL` | Logging verbosity |

## Usage

```bash
python -m audio_pipeline
```

You'll be prompted for the **word being recorded**, then for a short audio
capture. On first run, list your input devices, then set `INPUT_DEVICE` in
`.env` if the default isn't correct. Devices are always printed at startup.

### GUI mode

Launch a Tkinter UI with tap-to-record and adjustable length:

```bash
python -m audio_pipeline --gui
```

In the GUI:
- Enter the **word** to record — or a whole **sentence**, which is cut into
  words automatically (see below). The hint next to the field always says
  which of the two is about to happen.
- Set **Start numbering at** to control where auto-increment begins.
- Tap **Record** to start and tap **Record (Stop)** again to finish (freeform
  duration with a live timer), or check **Fixed duration** to record a set
  number of seconds.
- **Play** to hear the take, **Denoise** to apply noise reduction, then
  **Save** (writes `<n>.mp3` + index) or **Discard**.
- **Clean recordings** deletes all captured audio files and resets the index
  (with a confirmation prompt).

More commands: `python -m audio_pipeline --help`, `--list-devices`.

### Recording a whole sentence

There is no mode switch — **the text field decides**. Type one word and it
is saved as one file. Type two or more and the recording is cut into one
audio file per word on **Save**, with each word on its own line in the
index.

Type `amo ko amo`, say it, and you get three files and this index:

```ini
[am]
amo = 1, 3

[ko]
ko = 2
```

The repeated `amo` keeps **both** takes. A sentence never overwrites, so
the *Keep every take* and *Overwrite take* controls are greyed out while a
sentence is typed and come back unchanged when you go back to one word.

You say the words one per pause, and the cut points are found from the
pauses in your voice. Before anything is written, a dialog lists the
planned cut for every word with its start/end time; if the number of pauses
in the audio did not match the number of words you typed, that dialog says
so and you can discard and re-record. If words are being cut in half (or
run together), adjust `SEGMENT_SILENCE_RATIO` in `.env`.

No speech-to-text model is involved — the word names come from the
sentence you typed, and the cut points come from the waveform. If words
are being cut in half (or run together), adjust `SEGMENT_SILENCE_RATIO`
in `.env`.

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
└── index.txt            # word→number index (grouped by 2-letter prefix)
```

## Index format

`index.txt` is an INI-style text file grouping each recorded word by a
2-letter lowercase prefix and mapping it to the numeric stem of its audio file:

```ini
[ak]
ako = 4

[ko]
ko = 5

[si]
sa = 9
siya = 5
```

- **`siya = 5`** means the word "siya" is stored as `5.mp3`.
- Sections use the first `INDEX_PREFIX_LENGTH` letters (default 2).
- Numbers auto-increment globally; `INDEX_START_NUMBER` sets the starting
  value (useful when continuing an existing library). Re-recording the same
  word reuses its number — unless *Keep every take* is on, or the field
  holds a sentence, which always allocates a new number.
- Words are sorted alphabetically within each section.

## Project structure

```
audio_pipeline/         # package
├── __init__.py         # public exports + version
├── __main__.py         # entry point (python -m audio_pipeline [--gui])
├── config.py           # Settings + .env loading (python-dotenv)
├── models.py           # RecordingConfig, PipelineResult
├── index.py            # AudioIndex (word→number parse/render)
├── recorder.py         # AudioRecorder (sounddevice, fixed + streaming)
├── filter.py           # AudioFilter (noisereduce)
├── segmenter.py        # SentenceSegmenter (silence-based word cuts)
├── archiver.py         # AudioArchiver (pydub + index)
├── gui.py              # PipelineApp (Tkinter tap-to-record UI)
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
- A sentence adds one number per occurrence, so a word repeated in a
  sentence accumulates takes (`amo = 1, 3`) instead of overwriting itself.
- Tune filtering intensity via `prop_decrease`; the pipeline escalates to a
  configurable aggressive weight on subsequent passes.
