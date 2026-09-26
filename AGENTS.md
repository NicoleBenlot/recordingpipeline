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
- `INDEX_START_NUMBER` sets where auto-increment begins; `INDEX_PREFIX_LENGTH` (default 2) sets section grouping width.
- `MULTI_SPEAKER` (default 0) picks the dupe policy; `REDUCE_NOISE` (default 1) toggles background noise reduction. CLI overrides: `--multi-speaker`, `--no-filter`.

## Index format (critical — do not regress)

`index.txt` is INI-style, grouped by a lowercase prefix of the word (default
2 letters), mapping each word to **one or more** numeric stems of its audio files:

```ini
[si]
sa = 9
siya = 10
[am]
amo = 1, 2
```

Meaning `siya` is stored as `10.mp3`, while `amo` has two takes: `1.mp3` and
`2.mp3`. Rules:
- Each entry is `word = number[, number…]` under a `[prefix]` section (prefix length = `INDEX_PREFIX_LENGTH`, default 2). A word maps to `Dict[str, List[int]]` internally — a single number parses into a one-element list, so pre-multi-take index files load unchanged and still render identically.
- Words sorted alphabetically within each section; sections sorted; numbers ascending.
- Numbers auto-increment globally (`max+1` across every take of every word), starting at `INDEX_START_NUMBER`.
- NOT JSONL — an earlier version used JSONL; keep the INI format.

## Dupe words (two takes per word)

A word may hold several takes, typically one per speaker. `AudioArchiver.multi_speaker`
selects the policy, and it is decided in `resolve_target`, never in `register_word`:

- **Multi-speaker on** — an already-indexed word gets a fresh number, so nothing is overwritten: `amo = 1` + `amo = 1, 2` with `1.mp3` and `2.mp3`.
- **Multi-speaker off (default)** — an already-indexed word reuses a number and the audio file at that stem is replaced. `resolve_target(word, overwrite=n)` picks which take; `overwrite=None` means the latest. `SaveTarget.overwrites` is `True` when a save will destroy existing audio, so callers can confirm first (the GUI dropdown + `askyesno`, the CLI prompt in `_resolve_cli_target`).
- `register_word(word, number)` is **idempotent** — it only ensures `number` is in the word's list. That single call serves both policies: it appends after a multi-speaker save and is a no-op after an overwrite.
- `clean()` deletes every number of every word, so a multi-take word loses all of its files.

## Archiver sequencing (order matters)

`AudioArchiver` reads the index file from disk on **every** call, so:
1. `resolve_target(word, overwrite=None)` → a `SaveTarget` (number + what already exists), or `numbers_for_word(word)` to just read the take list
2. `save_as_mp3(audio, str(target.number))` → writes `<number>.mp3`
3. `register_word(word, number)` → persists the index

Number must be resolved and the audio saved before `register_word`. Do not batch
resolution calls ahead of `register_word` — each reloads the file, so unpersisted
increments cancel out. `number_for_word(word)` still exists as a thin shim over
`resolve_target` for callers that don't need the context.

## GUI (gui.py)

`PipelineApp` (Tkinter, launched via `--gui`) exposes a **Clean recordings**
button that calls `AudioArchiver.clean()` (deletes indexed `.mp3` files and the
index). The **Start numbering at** field updates `archiver.start_number` — read
it from the UI variable and push it to the archiver before any
`resolve_target`/`clean` call (see `_apply_start_number`).

Dupe + filter controls:
- **Keep every take (multi-speaker)** checkbutton → `_apply_multi_speaker()` pushes it onto `archiver.multi_speaker` and enables/disables the take picker.
- **Overwrite take** dropdown lists the takes already indexed for the word in the **Word to record** field, newest last and preselected. Populated by `_refresh_overwrite_choices()` on word `FocusOut`, after every save, and after `clean()`. `_selected_overwrite()` re-validates the choice against the index and returns `None` (fall back to the latest) if it went stale, so a stale dropdown can never clobber the wrong file.
- **Background noise reduction** checkbutton → when on, `_on_capture_done` denoises each capture automatically; when off the raw signal is archived. The manual **Denoise** button works either way.
