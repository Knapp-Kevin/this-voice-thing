"""The Voices page (the saved voice library), and the voice Generate is using."""

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer


class VoicePage:
    """The Voices page and the current reference clip. Mixed into ChatterboxApp."""

    def _build_voice_page(self):
        voice_page, voice_layout = self._make_page(
            "Voices", "Your saved voices. Click one to use it on Generate; make new ones in the Studio.")
        voice_layout.addWidget(self._build_library_card(), 1)
        self.pages.addWidget(voice_page)

    @property
    def reference_path(self):
        """The clip Generate clones from ("" for none): the one place this is kept."""
        return getattr(self, "_reference_path", "") or ""

    @staticmethod
    def transcript_path(audio_path):
        return os.path.splitext(audio_path)[0] + ".txt"

    def load_reference_transcript(self, audio_path):
        text = ""
        if audio_path and os.path.exists(self.transcript_path(audio_path)):
            with open(self.transcript_path(audio_path), encoding="utf-8") as handle:
                text = handle.read().strip()
        self.qwen_transcript_input.setText(text)

    def save_reference_transcript(self):
        audio_path = self.reference_path
        if not audio_path:
            return
        text = self.qwen_transcript_input.text().strip()
        path = self.transcript_path(audio_path)
        try:
            if text:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            print(f"Could not save transcript for {os.path.basename(audio_path)}: {exc}")

    # --- Voice selection ---

    def set_reference_audio(self, path):
        self._reference_path = path or ""
        if hasattr(self, "qwen_transcript_input"):
            self.load_reference_transcript(path)
            self.refresh_voice_chip()
        self.render_voice_tiles()

    def clear_reference_audio(self):
        self.stop_reference_preview()
        self.active_voice_id = None
        self.set_reference_audio(None)
        self.set_status_message("Status: Using the model's default voice.")

    def refresh_recordings_list(self):
        """Bring new recordings into the library and redraw it."""
        self.voice_library.import_recordings()
        self.render_voice_tiles()

    def stop_reference_preview(self):
        self.preview_player.stop()

    def _on_preview_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState and self.preview_button_playing:
            self.preview_button_playing = None

    def open_recordings_folder(self):
        os.makedirs(self.recordings_directory, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.recordings_directory))
