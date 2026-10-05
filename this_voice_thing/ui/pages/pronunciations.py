"""The pronunciation dictionary: its card, the editor, and auditioning respellings."""

import os
import tempfile

import numpy as np
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton

from this_voice_thing.ui.dialogs.pronunciation import PronunciationDialog
from this_voice_thing.ui.threads import SpeakThread
from this_voice_thing.ui.widgets import dialog_accepted


class Pronunciations:
    """The pronunciation dictionary. Mixed into ChatterboxApp."""

    def _build_pronunciation_card(self):
        pronunciation_card, pronunciation_layout = self._make_card("Pronunciation")
        pronunciation_row = QHBoxLayout()
        self.pronunciation_checkbox = QCheckBox("Use the pronunciation dictionary")
        self.pronunciation_checkbox.setChecked(self.pronunciations.enabled)
        self.pronunciation_checkbox.setToolTip(
            "Respell words before they're spoken (names, acronyms, jargon). Applies to every model; "
            "subtitles keep the original spelling.")
        self.pronunciation_checkbox.toggled.connect(self.on_pronunciation_toggled)
        pronunciation_row.addWidget(self.pronunciation_checkbox)
        self.pronunciation_summary = QLabel()
        self.pronunciation_summary.setObjectName("Muted")
        pronunciation_row.addWidget(self.pronunciation_summary)
        pronunciation_row.addStretch(1)
        edit_pronunciations = QPushButton("Edit dictionary...")
        edit_pronunciations.clicked.connect(self.edit_pronunciations)
        pronunciation_row.addWidget(edit_pronunciations)
        pronunciation_layout.addLayout(pronunciation_row)
        return pronunciation_card

    # --- Pronunciation dictionary ---

    def update_pronunciation_summary(self):
        rules = self.pronunciations.rules
        active = sum(1 for rule in rules if rule.enabled)
        self.pronunciation_summary.setText(
            f"{active} word{'s' if active != 1 else ''}" + (f" ({len(rules) - active} off)" if active != len(rules) else "")
            if rules else "Empty")

    def on_pronunciation_toggled(self, checked):
        self.pronunciations.enabled = checked
        self.pronunciations.save()
        self.update_text_stats()

    def edit_pronunciations(self):
        dialog = PronunciationDialog(self.pronunciations, self.speak_text, self)
        if dialog_accepted(dialog.exec()):
            self.pronunciations.set_rules(dialog.rules())
            self.pronunciations.save()
            self.update_pronunciation_summary()
            self.update_text_stats()
            self.set_status_message(f"Status: Saved {len(self.pronunciations.rules)} pronunciations.")

    def speak_text(self, text):
        """Say a short text with the loaded model and voice (used by Hear it)."""
        if self.model is None or self.model_busy() or not text.strip():
            self.set_status_message("Status: Load a model (and wait for any generation) to hear it.")
            return
        if getattr(self, "speak_thread", None) is not None and self.speak_thread.isRunning():
            return
        kwargs = {"language_id": self.language_combo.currentData() or "en"}
        reference = self.reference_path
        if self.active_qwen_model() is not None:
            problem = self.prepare_qwen_generation()
            if problem:
                QMessageBox.information(self, "Hear It", problem)
                return
        if reference:
            kwargs["audio_prompt_path"] = reference
        self.set_status_message(f"Status: Speaking \u201c{text[:40]}\u201d...")
        self.speak_thread = SpeakThread(self.model, text, kwargs, self)
        self.speak_thread.finished_with.connect(self.on_spoken)
        self.speak_thread.start()

    def on_spoken(self, wav, sr, error):
        if error or wav is None:
            self.set_status_message(f"Status: Couldn't speak it: {error}")
            return
        import soundfile
        path = os.path.join(tempfile.gettempdir(), "tts_pronunciation_check.wav")
        soundfile.write(path, np.clip(wav, -1.0, 1.0), sr, subtype="PCM_16")
        self.stop_reference_preview()
        self.preview_player.setSource(QUrl())
        self.preview_player.setSource(QUrl.fromLocalFile(path))
        self.preview_player.play()
        self.set_status_message("Status: Playing the pronunciation check.")
