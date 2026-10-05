"""The Transcribe page: speech to text with Whisper."""

import os
import re
import time

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
)

from this_voice_thing.core import documents, model_registry, subtitles, transcription, voice_library
from this_voice_thing.ui import theme as ui_theme
from this_voice_thing.ui.threads import TranscribeThread


class TranscribePage:
    """The Transcribe page: speech to text with Whisper. Mixed into ChatterboxApp."""

    def _build_transcribe_page(self):
        transcribe_page, transcribe_layout = self._make_page(
            "Transcribe", "Speech to text, on this PC, with Whisper.")
        audio_card, audio_layout = self._make_card("Audio")
        audio_row = QHBoxLayout()
        self.transcribe_source_label = QLabel("No audio chosen.")
        self.transcribe_source_label.setObjectName("Muted")
        self.transcribe_source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        audio_row.addWidget(self.transcribe_source_label, 1)
        use_clip_button = self._link(QPushButton("Use current voice clip"))
        use_clip_button.setToolTip("Transcribe the reference clip selected on the Voice page.")
        use_clip_button.clicked.connect(self.transcribe_use_voice_clip)
        audio_row.addWidget(use_clip_button)
        open_audio_button = QPushButton("Open audio...")
        open_audio_button.setToolTip("WAV, FLAC, OGG or MP3; M4A/AAC and others need FFmpeg.")
        open_audio_button.clicked.connect(self.transcribe_open_audio)
        audio_row.addWidget(open_audio_button)
        audio_layout.addLayout(audio_row)
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Language"))
        self.transcribe_language_combo = QComboBox()
        for code, name in transcription.LANGUAGES.items():
            self.transcribe_language_combo.addItem(name, code)
        self.transcribe_language_combo.setToolTip("Whisper detects the language by itself; pick one if it guesses wrong.")
        options_row.addWidget(self.transcribe_language_combo)
        self.transcribe_timestamps_checkbox = QCheckBox("Timestamps")
        self.transcribe_timestamps_checkbox.setToolTip("Show when each passage starts, e.g. [1:05].")
        self.transcribe_timestamps_checkbox.toggled.connect(lambda _on: self.show_transcript())
        options_row.addWidget(self.transcribe_timestamps_checkbox)
        options_row.addStretch(1)
        self.transcribe_button = self._accent(QPushButton("Transcribe"))
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.clicked.connect(self.start_transcription)
        options_row.addWidget(self.transcribe_button)
        audio_layout.addLayout(options_row)
        self.transcribe_progress = QProgressBar()
        self.transcribe_progress.setRange(0, 0)
        self.transcribe_progress.setFixedHeight(6)
        self.transcribe_progress.setTextVisible(False)
        self.transcribe_progress.setVisible(False)
        audio_layout.addWidget(self.transcribe_progress)
        transcribe_layout.addWidget(audio_card)
        text_card_t, text_layout_t = self._make_card("Text")
        self.transcript_output = QPlainTextEdit()
        self.transcript_output.setPlaceholderText("The transcript appears here. You can edit it before saving or sending it.")
        text_layout_t.addWidget(self.transcript_output, 1)
        self.transcribe_status = QLabel()
        self.transcribe_status.setObjectName("Muted")
        self.transcribe_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_layout_t.addWidget(self.transcribe_status)
        result_row = QHBoxLayout()
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(lambda: (QApplication.clipboard().setText(self.transcript_output.toPlainText()),
                                             self.set_status_message("Status: Copied the transcript.")))
        result_row.addWidget(copy_button)
        save_text_button = QPushButton("Save text...")
        save_text_button.clicked.connect(self.save_transcript_text)
        result_row.addWidget(save_text_button)
        self.save_subtitles_button = QPushButton("Save subtitles...")
        self.save_subtitles_button.setToolTip("SRT or WebVTT, timed from the audio.")
        self.save_subtitles_button.clicked.connect(self.save_transcript_subtitles)
        result_row.addWidget(self.save_subtitles_button)
        result_row.addStretch(1)
        send_button = self._link(QPushButton("Send to Generate"))
        send_button.setToolTip("Put this text on the Generate page, to speak it with any voice.")
        send_button.clicked.connect(self.send_transcript_to_generate)
        result_row.addWidget(send_button)
        text_layout_t.addLayout(result_row)
        transcribe_layout.addWidget(text_card_t, 1)
        self.pages.addWidget(transcribe_page)
        self.transcribe_source = None

    # --- Transcription ---

    def set_transcribe_source(self, path):
        self.transcribe_source = path
        saved = self.voice_library.find_clip(path) if path else None
        label = saved.name if saved else os.path.basename(path) if path else "No audio chosen."
        self.transcribe_source_label.setText(label)
        self.transcribe_source_label.setToolTip(path or "")
        self.transcribe_button.setEnabled(bool(path))

    def transcribe_open_audio(self):
        start = self.app_settings.get("last_transcribe_dir") or self.last_reference_audio_dir
        path, _filter = QFileDialog.getOpenFileName(self, "Open audio to transcribe", start, transcription.AUDIO_FILTER)
        if path:
            self.app_settings["last_transcribe_dir"] = os.path.dirname(path)
            self.set_transcribe_source(path)

    def transcribe_use_voice_clip(self):
        path = self.reference_path
        if not path:
            QMessageBox.information(self, "Transcribe", "Pick a voice clip on the Voice page first.")
            return
        self.set_transcribe_source(path)

    def confirm_whisper_download(self):
        if transcription.is_downloaded():
            return True
        answer = QMessageBox.question(
            self, "Transcription",
            f"Transcription uses OpenAI's {transcription.MODEL_LABEL} (MIT license). It runs on this PC; "
            f"the model downloads once, about {model_registry.format_size(transcription.DOWNLOAD_BYTES)}.\n\n"
            "Download it now?")
        return answer == QMessageBox.StandardButton.Yes

    def run_transcription(self, source, language, task, on_done, status):
        if getattr(self, "transcribe_thread", None) is not None and self.transcribe_thread.isRunning():
            self.set_status_message("Status: A transcription is already running.")
            return False
        if not self.confirm_whisper_download():
            return False
        self.set_status_message(status)
        self.transcribe_thread = TranscribeThread(self.transcriber, source, language, task, self)
        self.transcribe_thread.done.connect(on_done)
        self.transcribe_thread.start()
        return True

    def start_transcription(self):
        if not self.transcribe_source:
            return
        started = time.monotonic()

        def done(transcript, error):
            self.transcribe_progress.setVisible(False)
            self.transcribe_button.setEnabled(True)
            if error:
                self.transcribe_status.setText(f"Couldn't transcribe: {error}")
                ui_theme.set_tone(self.transcribe_status, "error")
                self.set_status_message("Status: Transcription failed. See the Log page.")
                return
            self.transcript = transcript
            self.show_transcript()
            ui_theme.set_tone(self.transcribe_status, "")
            self.transcribe_status.setText(
                f"{self.format_duration(transcript.seconds)} of audio in "
                f"{self.format_duration(time.monotonic() - started)} \u00b7 {len(transcript.segments)} passage{'s' if len(transcript.segments) != 1 else ''} "
                f"\u00b7 {transcription.MODEL_LABEL} on {self.transcriber.device}")
            self.set_status_message("Status: Transcription ready.")

        language = self.transcribe_language_combo.currentData()
        if self.run_transcription(self.transcribe_source, language, "transcribe", done,
                                  "Status: Transcribing (the first time also loads Whisper)..."):
            self.transcribe_button.setEnabled(False)
            self.transcribe_progress.setVisible(True)
            self.transcribe_status.setText("Transcribing...")

    def show_transcript(self):
        if self.transcript is None:
            return
        self.transcript_output.setPlainText(
            transcription.timestamped_text(self.transcript)
            if self.transcribe_timestamps_checkbox.isChecked() and self.transcript.segments else self.transcript.text)
        self.save_subtitles_button.setEnabled(bool(self.transcript.segments))

    def transcript_stem(self):
        return documents.safe_file_stem(os.path.splitext(os.path.basename(self.transcribe_source or "transcript"))[0])

    def save_transcript_text(self):
        text = self.transcript_output.toPlainText().strip()
        if not text:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save transcript", os.path.join(self.output_directory, self.transcript_stem() + ".txt"), "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
            self.set_status_message(f"Status: Saved {os.path.basename(path)}.")

    def save_transcript_subtitles(self):
        if self.transcript is None or not self.transcript.segments:
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save subtitles", os.path.join(self.output_directory, self.transcript_stem() + ".srt"),
            "SubRip (*.srt);;WebVTT (*.vtt)")
        if path:
            subtitle_format = "WebVTT" if path.lower().endswith(".vtt") or "vtt" in chosen.lower() else "SRT"
            saved = subtitles.save(os.path.splitext(path)[0], transcription.to_cues(self.transcript), subtitle_format)
            self.set_status_message(f"Status: Saved {os.path.basename(saved)}.")

    def send_transcript_to_generate(self):
        # Speak the words, not the "[1:05]" timestamps.
        text = re.sub(r"(?m)^\[\d+:\d\d\]\s*", "", self.transcript_output.toPlainText()).strip()
        if not text:
            return
        self.text_input.setPlainText(text)
        self.current_document_name = None
        self.document_label.setText("From Transcribe")
        self.sidebar.setCurrentRow(self.PAGE_GENERATE)
        self.set_status_message("Status: Transcript sent to Generate.")

    def transcribe_voice_clip(self, voice):
        """Fill in a library clip's transcript (cloning models such as OmniVoice need one)."""
        path = self.voice_library.clip_path(voice)

        def done(transcript, error):
            if error or transcript is None:
                self.set_status_message(f"Status: Couldn't transcribe {voice.name}: {error}")
                return
            voice_library.write_transcript(path, transcript.text)
            if self.voice_is_active(voice):
                self.load_reference_transcript(path)
            self.render_voice_tiles()
            self.set_status_message(f"Status: Transcribed {voice.name}. Check it with Edit... if a word is off.")

        self.run_transcription(path, "auto", "transcribe", done, f"Status: Transcribing {voice.name}...")
