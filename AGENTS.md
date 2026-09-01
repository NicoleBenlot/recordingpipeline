# AGENTS.md

Python 3.9+ package for recording, noise-filtering, reviewing, and archiving
audio words into numeric `.mp3` files with an INI-style index.

## Commands

- Install deps: `pip install -r requirements.txt`
- Run: `python -m audio_pipeline` (it is a **package**, not a script — `python audio_pipeline.py` will not work)
- GUI mode: `python -m audio_pipeline --gui` (Tkinter tap-to-record); `--list-devices` lists devices, `--help` for flags
- No tests, lint, typecheck, or CI configured. Only sanity check: `python -m py_compile audio_pipeline\*.py` fails on Windows globbing — compile each file explicitly instead.

## Non-obvious setup / gotchas

- **MP3 encoding needs `ffmpeg` on PATH** (pydub). Not listed in requirements. `ensure_ffmpeg_on_path()` in `archiver.py` auto-locates the WinGet `Gyan.FFmpeg` bin dir and prepends it to `PATH` on init, so it works from a fresh shell. On a non-WinGet install, ffmpeg must already be on PATH.
- **Windows console defaults to cp1252** and crashes (`UnicodeEncodeError`) on the box-drawing glyphs in the CLI. `_ensure_utf8()` in `audio_pipeline/__main__.py` must run before any print.
- **`__init__.py` eagerly imports every module**, so importing anything from the package requires all audio deps (sounddevice, numpy, noisereduce, pydub) to be installed.
- `.env` is loaded via `python-dotenv` in `config.py`; `.env` is gitignored. Copy `.env.example` to `.env` to configure.

## Architecture

Each concern is one module under `audio_pipeline/`:
- `recorder.py` — `AudioRecorder` (sounddevice) record + playback
- `filter.py` — `AudioFilter` (noisereduce spectral gating)
- `archiver.py` — `AudioArchiver`: mp3 encode + the word→number index
- `index.py` — `AudioIndex`: parse/render of the index text
- `pipeline.py` — `AudioPipeline` orchestrator: Record → Filter → Review → Archive
- `config.py` — `Settings`/`load_settings` (.env)
- `models.py` — `RecordingConfig`, `PipelineResult`
- `gui.py` — `PipelineApp` (Tkinter tap-to-record UI); optional, guarded in `__init__.py`
- `__main__.py` — entry point

## Storage layout (`.env`)

- `ARTIFACT_ROOT` (absolute, takes precedence) or `OUTPUT_DIR` sets the index destination.
- `AUDIO_SUBDIR` puts `.mp3` in a subfolder; the index stays at the root.
- `INDEX_FILENAME` names the index file (e.g. `index.txt`).

## Index format (critical — do not regress)

`index.txt` is INI-style, grouped by first letter of the word, mapping each word
to the numeric stem of its audio file:

```ini
[s]
sa = 9
siya = 10
```

Meaning `siya` is stored as `10.mp3`. Rules:
- Each entry is `word = number` under a `[letter]` section.
- Words sorted alphabetically within each section; sections sorted.
- Numbers auto-increment globally (`max+1`); **re-recording a word reuses its number**.
- NOT JSONL — an earlier version used JSONL; keep the INI format.

## Archiver sequencing (order matters)

`AudioArchiver` reads the index file from disk on **every** call, so:
1. `number_for_word(word)` → returns the number (may be existing or new auto-increment)
2. `save_as_mp3(audio, str(number))` → writes `<number>.mp3`
3. `register_word(word, number)` → persists the index

Number must be resolved and the mp3 saved before `register_word`. Do not batch
`number_for_word` calls ahead of `register_word` — each reloads the file, so
unpersisted increments cancel out.
