"""Tkinter GUI for the audio capture pipeline.

Provides a tap-to-record / tap-again-to-stop interface with a live
elapsed timer, optional fixed-duration recording, playback, noise
reduction, and save/discard controls wired into the backend classes.

Dupe words are handled by two controls: *Keep every take
(multi-speaker)* allocates a fresh number per recording
(``amo = 1, 2``), while with it off the *Overwrite take* dropdown —
populated from the takes already indexed for the word in the entry
field — selects which existing ``<number>.<ext>`` file to replace.
*Background noise reduction* toggles the automatic denoise pass that
runs after each capture; the manual Denoise button is always available.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

import numpy as np

from .config import Settings
from .archiver import AudioArchiver, SaveTarget
from .recorder import AudioRecorder
from .filter import AudioFilter

logger: logging.Logger = logging.getLogger(__name__)


class PipelineApp:
    """Main Tkinter window.

    Parameters
    ----------
    settings : Settings
        Typed pipeline settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings: Settings = settings

        # Backend classes wired to settings (paths, devices, sample rate).
        self.recorder: AudioRecorder = AudioRecorder(
            sample_rate=settings.sample_rate,
            channels=settings.channels,
            device=settings.input_device,
        )
        self.filter: AudioFilter = AudioFilter(
            sample_rate=settings.sample_rate,
            prop_decrease=settings.filter_prop_decrease,
        )
        self.archiver: AudioArchiver = AudioArchiver(
            output_dir=str(settings.resolved_destination),
            audio_dir=str(settings.resolved_audio_dir),
            index_filename=settings.index_filename,
            audio_format=settings.audio_format,
            bitrate=settings.mp3_bitrate,
            sample_rate=settings.sample_rate,
            start_number=settings.index_start_number,
            prefix_length=settings.index_prefix_length,
            multi_speaker=settings.multi_speaker,
        )
        self._start_number_base: int = settings.index_start_number

        # Takes currently indexed for the word in the entry field.
        self._overwrite_numbers: List[int] = []
        self._overwrite_choices: List[str] = []

        # Current working buffer (raw or filtered) awaiting review.
        self.current_audio: Optional[np.ndarray] = None
        self.filtered: bool = False
        self._elapsed_tick: Optional[str] = None

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Construct all widgets and layout."""
        self.root = tk.Tk()
        self.root.title("Audio Capture Pipeline")
        self.root.resizable(False, False)

        main = ttk.Frame(self.root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")

        # ---- Word / notes --------------------------------------------
        ttk.Label(main, text="Word to record:").grid(row=0, column=0, sticky="w")
        self.word_var = tk.StringVar()
        self.word_entry = ttk.Entry(
            main, textvariable=self.word_var, width=28
        )
        self.word_entry.grid(row=0, column=1, columnspan=2, sticky="we", padx=6)
        # Leaving the field is when the take list can be looked up.
        self.word_entry.bind("<FocusOut>", self._on_word_focus_out)

        # ---- Start numbering at --------------------------------------
        ttk.Label(
            main, text="Start numbering at:"
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.start_number_var = tk.StringVar(
            value=str(self._start_number_base)
        )
        self.start_number_entry = ttk.Entry(
            main, textvariable=self.start_number_var, width=8
        )
        self.start_number_entry.grid(
            row=1, column=1, sticky="w", padx=6, pady=(12, 0)
        )
        self.start_hint = ttk.Label(
            main,
            text="New words auto-increment from here.",
            foreground="#666",
        )
        self.start_hint.grid(row=1, column=2, sticky="w", pady=(12, 0))

        # ---- Audio format ---------------------------------------------
        ttk.Label(
            main, text="Audio format:"
        ).grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.format_var = tk.StringVar(
            value=self.archiver.audio_format
        )
        self.format_box = ttk.Combobox(
            main,
            textvariable=self.format_var,
            values=["mp3", "opus", "ogg", "wav"],
            state="readonly",
            width=8,
        )
        self.format_box.grid(
            row=2, column=1, sticky="w", padx=6, pady=(12, 0)
        )
        self.format_hint = ttk.Label(
            main,
            text="opus/ogg need an opusenc/oggenc binary (ffmpeg).",
            foreground="#666",
        )
        self.format_hint.grid(row=2, column=2, sticky="w", pady=(12, 0))

        # ---- Dupe policy ----------------------------------------------
        self.multi_speaker_var = tk.BooleanVar(
            value=self.archiver.multi_speaker
        )
        ttk.Checkbutton(
            main,
            text="Keep every take (multi-speaker)",
            variable=self.multi_speaker_var,
            command=self._apply_multi_speaker,
        ).grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.multi_hint = ttk.Label(
            main,
            text="amo = 1, 2 → 1.opus + 2.opus",
            foreground="#666",
        )
        self.multi_hint.grid(row=3, column=1, columnspan=2, sticky="w", pady=(12, 0))

        # ---- Take picker (overwrite mode) ------------------------------
        ttk.Label(
            main, text="Overwrite take:"
        ).grid(row=4, column=0, sticky="w", pady=(12, 0))
        self.overwrite_var = tk.StringVar()
        self.overwrite_box = ttk.Combobox(
            main,
            textvariable=self.overwrite_var,
            values=[],
            state="readonly",
            width=8,
        )
        self.overwrite_box.grid(
            row=4, column=1, sticky="w", padx=6, pady=(12, 0)
        )
        self.overwrite_box.state(["disabled"])
        self.overwrite_hint = ttk.Label(
            main,
            text="No takes indexed for this word yet.",
            foreground="#666",
        )
        self.overwrite_hint.grid(row=4, column=2, sticky="w", pady=(12, 0))

        # ---- Duration mode --------------------------------------------
        self.fixed_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            main,
            text="Fixed duration (seconds)",
            variable=self.fixed_mode,
            command=self._on_mode_toggle,
        ).grid(row=5, column=0, sticky="w", pady=(12, 0))

        self.duration_var = tk.StringVar(
            value=str(self.settings.duration_seconds)
        )
        self.duration_entry = ttk.Entry(
            main, textvariable=self.duration_var, width=8
        )
        self.duration_entry.grid(row=5, column=1, sticky="w", padx=6, pady=(12, 0))
        self.duration_entry.state(["disabled"])
        self.mode_hint = ttk.Label(
            main,
            text="Tap Record to start, tap again to stop (freeform).",
            foreground="#666",
        )
        self.mode_hint.grid(row=5, column=2, sticky="w", pady=(12, 0))

        # ---- Background noise reduction -------------------------------
        self.reduce_noise_var = tk.BooleanVar(value=self.settings.reduce_noise)
        ttk.Checkbutton(
            main,
            text="Background noise reduction",
            variable=self.reduce_noise_var,
        ).grid(row=6, column=0, sticky="w", pady=(12, 0))
        self.filter_hint = ttk.Label(
            main,
            text="Applies automatically after each capture.",
            foreground="#666",
        )
        self.filter_hint.grid(row=6, column=1, columnspan=2, sticky="w", pady=(12, 0))

        # ---- Big Record button ----------------------------------------
        self.record_btn = ttk.Button(
            main,
            text="●  Record",
            command=self._on_record_click,
            width=20,
        )
        self.record_btn.grid(row=7, column=0, columnspan=3, pady=16)

        self.timer_label = ttk.Label(
            main, text="00:00.0", font=("Consolas", 22)
        )
        self.timer_label.grid(row=8, column=0, columnspan=3)

        self.status_label = ttk.Label(
            main, text="Ready.", foreground="#555"
        )
        self.status_label.grid(row=9, column=0, columnspan=3, pady=(6, 0))

        # ---- Review controls -------------------------------------------
        review = ttk.Frame(main)
        review.grid(row=10, column=0, columnspan=3, pady=(18, 0))

        self.play_btn = ttk.Button(
            review, text="Play", command=self._on_play_click
        )
        self.play_btn.grid(row=0, column=0, padx=4)

        self.filter_btn = ttk.Button(
            review, text="Denoise", command=self._on_denoise_click
        )
        self.filter_btn.grid(row=0, column=1, padx=4)

        self.save_btn = ttk.Button(
            review, text="Save", command=self._on_save_click
        )
        self.save_btn.grid(row=0, column=2, padx=4)

        self.discard_btn = ttk.Button(
            review, text="Discard", command=self._on_discard_click
        )
        self.discard_btn.grid(row=0, column=3, padx=4)

        for widget in (self.play_btn, self.filter_btn, self.save_btn,
                       self.discard_btn):
            widget.state(["disabled"])

        # Where assets land + clean button
        ttk.Separator(main).grid(row=11, column=0, columnspan=3,
                                  sticky="we", pady=12)
        ttk.Label(
            main,
            text=f"→ {self.settings.resolved_audio_dir}",
            foreground="#888",
        ).grid(row=12, column=0, columnspan=2, sticky="w")

        self.clean_btn = ttk.Button(
            main, text="Clean recordings", command=self._on_clean_click
        )
        self.clean_btn.grid(row=12, column=2, sticky="e")

        # Populate the take picker for any word already in the field.
        self._refresh_overwrite_choices()

    # ------------------------------------------------------------------
    def _on_word_focus_out(self, _event: Optional[tk.Event] = None) -> None:
        """Refresh the take picker when the word field loses focus."""
        self._refresh_overwrite_choices()

    # ------------------------------------------------------------------
    def _refresh_overwrite_choices(self) -> None:
        """Repopulate the take picker from the word in the entry field.

        Reads every take already indexed for that word so the dropdown
        can offer each ``<number>.<ext>`` file as an overwrite target.
        The latest take is preselected.
        """
        word: str = self.word_var.get().strip()
        if not word:
            self._set_overwrite_choices([])
            return
        try:
            existing: List[int] = self.archiver.numbers_for_word(word)
        except Exception as exc:
            logger.error("Could not read the index for '%s': %s", word, exc)
            self._set_overwrite_choices([])
            return
        self._set_overwrite_choices(existing)

    # ------------------------------------------------------------------
    def _set_overwrite_choices(self, numbers: List[int]) -> None:
        """Show *numbers* as the available overwrite targets.

        Parameters
        ----------
        numbers : List[int]
            Take numbers indexed for the current word.  Empty disables
            the dropdown.
        """
        labels: List[str] = [str(n) for n in numbers]
        self._overwrite_numbers = list(numbers)
        self._overwrite_choices = labels
        self.overwrite_box.configure(values=labels)

        if not labels:
            self.overwrite_var.set("")
            self.overwrite_box.state(["disabled"])
            self.overwrite_hint.config(
                text="No takes indexed for this word yet."
            )
            return

        # Keep a valid selection, defaulting to the latest take.
        if self.overwrite_var.get() not in labels:
            self.overwrite_var.set(labels[-1])

        if self.multi_speaker_var.get():
            self.overwrite_box.state(["disabled"])
            self.overwrite_hint.config(
                text="Multi-speaker on: nothing is overwritten."
            )
        else:
            self.overwrite_box.state(["!disabled"])
            self.overwrite_hint.config(
                text=f"{len(labels)} take(s) indexed; latest is {labels[-1]}."
            )

    # ------------------------------------------------------------------
    def _apply_multi_speaker(self) -> None:
        """Push the multi-speaker checkbox onto the archiver.

        The take picker only applies when takes are replaced, so it is
        disabled while multi-speaker mode is on.
        """
        self.archiver.multi_speaker = self.multi_speaker_var.get()
        self._set_overwrite_choices(self._overwrite_numbers)

    # ------------------------------------------------------------------
    def _on_mode_toggle(self) -> None:
        """Enable/disable the duration field when the mode changes."""
        state = "normal" if self.fixed_mode.get() else "disabled"
        self.duration_entry.state([state])
        if self.fixed_mode.get():
            self.mode_hint.config(text="Records for the set duration.")
        else:
            self.mode_hint.config(
                text="Tap Record to start, tap again to stop (freeform)."
            )

    # ------------------------------------------------------------------
    def _on_record_click(self) -> None:
        """Toggle recording: start a stream or stop it."""
        if self.recorder.is_streaming:
            self._stop_recording()
            return
        self._start_recording()

    # ------------------------------------------------------------------
    def _start_recording(self) -> None:
        """Begin capturing audio (validates the word and mode first)."""
        word: str = self.word_var.get().strip()
        if not word:
            messagebox.showwarning(
                "Missing word", "Enter the word being recorded first."
            )
            return

        if self.fixed_mode.get():
            try:
                duration: float = float(self.duration_var.get())
                if duration <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Invalid duration",
                    "Duration must be a positive number of seconds.",
                )
                return
            self._record_fixed(duration)
        else:
            self._start_freeform()

    # ------------------------------------------------------------------
    def _start_freeform(self) -> None:
        """Open a background stream; elapsed time updates via a ticker."""
        self._set_status("Recording… tap Record to stop.", "red")
        self.record_btn.config(text="■  Stop")
        self._disable_review()
        try:
            self.recorder.start_stream()
        except Exception as exc:
            messagebox.showerror("Record error", str(exc))
            self._set_status("Ready.", "#555")
            self.record_btn.config(text="●  Record")
            return
        self._tick_timer()

    # ------------------------------------------------------------------
    def _record_fixed(self, duration: float) -> None:
        """Record for *duration* seconds (blocking, on the UI thread).

        Parameters
        ----------
        duration : float
            Length of the recording.
        """
        self._set_status(f"Recording for {duration:g}s…", "red")
        self.record_btn.config(state=["disabled"])
        self._disable_review()

        def worker() -> None:
            try:
                audio: np.ndarray = self.recorder.record(duration)
                self.root.after(0, lambda: self._on_capture_done(audio))
            except Exception as exc:
                self.root.after(
                    0, lambda: self._on_capture_error(exc)
                )

        import threading
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    def _tick_timer(self) -> None:
        """Update the elapsed timer while freeform recording is active."""
        if not self.recorder.is_streaming:
            return
        elapsed: float = self.recorder.stream_duration
        mins, secs = divmod(int(elapsed), 60)
        tenths: int = int((elapsed - int(elapsed)) * 10)
        self.timer_label.config(text=f"{mins:02d}:{secs:02d}.{tenths}")
        self._elapsed_tick = self.root.after(100, self._tick_timer)

    # ------------------------------------------------------------------
    def _stop_recording(self) -> None:
        """Stop a freeform stream and finalize the captured buffer."""
        if self._elapsed_tick is not None:
            self.root.after_cancel(self._elapsed_tick)
            self._elapsed_tick = None
        audio: Optional[np.ndarray] = None
        try:
            audio = self.recorder.stop_stream()
        except Exception as exc:
            messagebox.showerror("Stop error", str(exc))
        self.record_btn.config(text="●  Record")
        if audio is not None:
            self._on_capture_done(audio)
        else:
            self._set_status("No audio captured.", "#555")
            self.timer_label.config(text="00:00.0")

    # ------------------------------------------------------------------
    def _on_capture_done(self, audio: np.ndarray) -> None:
        """Accepted the capture; enable review controls and timer stop."""
        if self._elapsed_tick is not None:
            self.root.after_cancel(self._elapsed_tick)
            self._elapsed_tick = None
        duration: float = len(audio) / self.recorder.sample_rate
        mins, secs = divmod(int(duration), 60)
        tenths: int = int((duration - int(duration)) * 10)
        self.timer_label.config(text=f"{mins:02d}:{secs:02d}.{tenths}")
        denoised: bool = False
        if self.reduce_noise_var.get():
            self._set_status("Reducing background noise…", "#00a")
            self.root.update_idletasks()
            audio = self.filter.reduce_noise(audio)
            denoised = True
        self.current_audio = audio
        self.filtered = denoised
        self.record_btn.config(text="●  Record", state=["normal"])
        note: str = " (denoised)" if denoised else ""
        self._set_status(
            f"Captured {duration:.1f}s{note}. Review, then Save or Discard.",
            "#0a0",
        )
        self._enable_review()

    # ------------------------------------------------------------------
    def _on_capture_error(self, exc: Exception) -> None:
        """Report a recording failure and reset the UI."""
        messagebox.showerror("Recording failed", str(exc))
        self.record_btn.config(text="●  Record", state=["normal"])
        self._set_status("Ready.", "#555")
        self.timer_label.config(text="00:00.0")

    # ------------------------------------------------------------------
    def _on_play_click(self) -> None:
        """Play the current buffer back through the speakers."""
        if self.current_audio is None:
            return
        try:
            self.recorder.play(self.current_audio)
        except Exception as exc:
            messagebox.showerror("Playback error", str(exc))

    # ------------------------------------------------------------------
    def _on_denoise_click(self) -> None:
        """Apply noise reduction to the current buffer."""
        if self.current_audio is None:
            return
        self._set_status("Denoising…", "#00a")
        self.current_audio = self.filter.reduce_noise(self.current_audio)
        self.filtered = True
        self._set_status("Noise reduction applied.", "#0a0")

    # ------------------------------------------------------------------
    def _selected_overwrite(self, word: str) -> Optional[int]:
        """Return the take the user picked, or ``None`` for the default.

        ``None`` lets :meth:`AudioArchiver.resolve_target` choose: the
        latest take for an indexed word, or a fresh number for a new one.
        A selection that no longer matches the index is discarded, so a
        stale dropdown value can never clobber the wrong file.

        Parameters
        ----------
        word : str
            The word being recorded.

        Returns
        -------
        Optional[int]
            The chosen take number, or ``None``.
        """
        if self.archiver.multi_speaker:
            return None
        raw: str = self.overwrite_var.get().strip()
        if not raw:
            return None
        try:
            chosen: int = int(raw)
        except ValueError:
            return None
        if chosen in self._overwrite_numbers:
            return chosen
        logger.warning(
            "Dropping stale take selection %s for '%s'", raw, word
        )
        return None

    # ------------------------------------------------------------------
    def _confirm_overwrite(self, target: SaveTarget) -> bool:
        """Ask before replacing the audio file behind *target*.

        The take picker is a deliberate choice, but the audio it destroys
        cannot be recovered, so the destructive step is confirmed.

        Parameters
        ----------
        target : SaveTarget
            A resolved target whose ``overwrites`` flag is set.

        Returns
        -------
        bool
            True when the user accepts.
        """
        ext: str = self.archiver.ext
        takes: str = ", ".join(
            "{0}.{1}".format(n, ext) for n in target.existing
        )
        return messagebox.askyesno(
            "Overwrite recording",
            f"'{target.word}' already has {len(target.existing)} "
            f"indexed take(s):\n  {takes}\n\n"
            f"Overwrite {target.number}.{ext}?\n"
            "The existing audio will be lost.",
        )

    # ------------------------------------------------------------------
    def _on_save_click(self) -> None:
        """Archive the current buffer and index it under the word."""
        if self.current_audio is None:
            return
        word: str = self.word_var.get().strip()
        if not word:
            messagebox.showwarning("Missing word", "Enter the word to record.")
            return
        # Apply the start-number the user set in the field.
        self._apply_start_number()
        # Apply the format selected in the dropdown.
        self._apply_format()
        # Apply the dupe policy and refresh the take list.
        self._apply_multi_speaker()
        self._refresh_overwrite_choices()
        try:
            target: SaveTarget = self.archiver.resolve_target(
                word, self._selected_overwrite(word)
            )
        except Exception as exc:
            logger.error("Index lookup failed: %s", exc)
            messagebox.showerror("Save failed", str(exc))
            return

        if target.overwrites and not self._confirm_overwrite(target):
            self._set_status("Save cancelled.", "#555")
            return

        try:
            self.archiver.save_as_mp3(self.current_audio, str(target.number))
            numbers: List[int] = self.archiver.register_word(
                word, target.number
            )
        except Exception as exc:
            logger.error("Save failed: %s", exc)
            messagebox.showerror("Save failed", str(exc))
            return

        ext: str = self.archiver.ext
        self._set_status(
            f"Saved '{target.word}' → {target.number}.{ext} "
            f"({len(numbers)} take(s): {', '.join(str(n) for n in numbers)}).",
            "#a50" if target.overwrites else "#0a0",
        )
        self.current_audio = None
        self._disable_review()
        self.timer_label.config(text="00:00.0")
        # The take list just changed; repopulate the picker.
        self._refresh_overwrite_choices()

    # ------------------------------------------------------------------
    def _apply_start_number(self) -> None:
        """Read the start-number field and push it onto the archiver.

        Parses the user input; on invalid input, resets the field to the
        last known-good value.
        """
        try:
            self.archiver.start_number = int(self.start_number_var.get())
        except ValueError:
            self.start_number_var.set(str(self.archiver.start_number))

    # ------------------------------------------------------------------
    def _apply_format(self) -> None:
        """Push the dropdown's format selection onto the archiver.

        Updates ``audio_format`` and the derived file extension so saved
        files (and ``clean``) use the selected container/codec.
        """
        selected: str = self.format_var.get().strip().lower()
        if selected:
            self.archiver.audio_format = selected
            self.archiver.ext = selected

    # ------------------------------------------------------------------
    def _on_clean_click(self) -> None:
        """Delete recorded audio files and reset the index (with confirm)."""
        if not messagebox.askyesno(
            "Clean recordings",
            "Delete all recorded audio files and reset the index?\n\n"
            f"Folder: {self.settings.resolved_audio_dir}",
        ):
            return
        self._apply_start_number()
        self._apply_format()
        removed: int = self.archiver.clean()
        self.status_label.config(
            text=f"Cleaned {removed} file(s). Index reset.", foreground="#555"
        )
        self.current_audio = None
        self._disable_review()
        self.timer_label.config(text="00:00.0")
        self._refresh_overwrite_choices()

    # ------------------------------------------------------------------
    def _on_discard_click(self) -> None:
        """Discard the current buffer without saving."""
        self.current_audio = None
        self._disable_review()
        self._set_status("Recording discarded.", "#555")
        self.timer_label.config(text="00:00.0")

    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str = "#555") -> None:
        """Update the status label text and color.

        Parameters
        ----------
        text : str
            Message to display.
        color : str
            Hex color for the text.
        """
        self.status_label.config(text=text, foreground=color)

    # ------------------------------------------------------------------
    def _enable_review(self) -> None:
        """Enable the review buttons."""
        for widget in (self.play_btn, self.filter_btn, self.save_btn,
                       self.discard_btn):
            widget.state(["!disabled"])

    # ------------------------------------------------------------------
    def _disable_review(self) -> None:
        """Disable the review buttons."""
        for widget in (self.play_btn, self.filter_btn, self.save_btn,
                       self.discard_btn):
            widget.state(["disabled"])

    # ------------------------------------------------------------------
    def run(self) -> None:
        """Start the Tk event loop and keep the window open."""
        self.root.mainloop()
