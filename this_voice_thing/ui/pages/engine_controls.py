"""Engine-specific Generate controls: Qwen, Kokoro, VoxCPM, OmniVoice and the VibeVoice cast."""

import os

from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtWidgets import QWidget

from this_voice_thing.core import documents
from this_voice_thing.engines import kokoro as kokoro_engine
from this_voice_thing.engines import omnivoice as omnivoice_engine
from this_voice_thing.engines import vibevoice as vibevoice_engine
from this_voice_thing.engines import voxcpm as voxcpm_engine
from this_voice_thing.ui.common import DUAL_MODE_TYPES
from this_voice_thing.ui.common import WORKER_MODEL_TYPES
from this_voice_thing.ui.dialogs.voices import CastDialog
from this_voice_thing.ui.widgets import dialog_accepted


class EngineControls:
    """Engine-specific Generate controls: Qwen, Kokoro, VoxCPM, OmniVoice and the VibeVoice cast. Mixed into ChatterboxApp."""

    # --- Engine-specific controls ---

    def active_qwen_model(self):
        """The loaded worker-engine model (Qwen or Kokoro), which uses the speaker/style row."""
        return self.model if isinstance(self.model, WORKER_MODEL_TYPES) else None

    def engine_settings(self, model):
        if isinstance(model, kokoro_engine.KokoroModel):
            return self.kokoro_settings
        if isinstance(model, voxcpm_engine.VoxCPMModel):
            return self.voxcpm_settings
        if isinstance(model, omnivoice_engine.OmniVoiceModel):
            return self.omnivoice_settings
        if isinstance(model, vibevoice_engine.VibeVoiceModel):
            return self.vibevoice_settings
        return self.qwen_settings

    def fill_speaker_combo(self, model):
        """Built-in voices; Kokoro's are filtered to the selected language."""
        kokoro = isinstance(model, kokoro_engine.KokoroModel)
        language = self.language_combo.currentData() or "en"
        speakers = model.voices_for(language) if kokoro else model.speakers
        if kokoro:
            saved = self.kokoro_settings.get("voice_by_language", {}).get(language)
        else:
            saved = self.qwen_settings.get("speaker")
        self.qwen_speaker_combo.blockSignals(True)
        self.qwen_speaker_combo.clear()
        for speaker in speakers:
            label = model.speaker_label(speaker) if kokoro else speaker.replace("_", " ").title()
            self.qwen_speaker_combo.addItem(label, speaker)
        index = self.qwen_speaker_combo.findData(saved)
        if index < 0 and kokoro:
            index = self.qwen_speaker_combo.findData("af_heart")
        self.qwen_speaker_combo.setCurrentIndex(max(0, index))
        self.qwen_speaker_combo.setToolTip(
            "Built-in Kokoro voice for the selected language." if kokoro else "Built-in Qwen speaker.")
        self.qwen_speaker_combo.blockSignals(False)
        self.refresh_voice_chip()

    def on_language_changed(self):
        if isinstance(self.model, kokoro_engine.KokoroModel) and hasattr(self, "qwen_speaker_combo"):
            self.fill_speaker_combo(self.model)

    def update_engine_controls(self):
        qwen = self.active_qwen_model()
        self.qwen_row.setVisible(qwen is not None)
        self.qwen_watermark_checkbox.setVisible(qwen is not None)
        for widget in (self.exaggeration_label, self.exaggeration_slider, self.cfg_label, self.cfg_slider):
            widget.setVisible(qwen is None)
        if qwen is not None:
            mode = qwen.mode
            self.fill_speaker_combo(qwen)
            self.qwen_watermark_checkbox.setChecked(bool(self.engine_settings(qwen).get("watermark", True)))
            settings = self.engine_settings(qwen)
            voxcpm = isinstance(qwen, voxcpm_engine.VoxCPMModel)
            omnivoice = isinstance(qwen, omnivoice_engine.OmniVoiceModel)
            if mode == "voice_design" and omnivoice:
                self.qwen_instruct_label.setText("Voice attributes")
                self.qwen_instruct_input.setPlaceholderText("e.g. female, young adult, low pitch, british accent")
                self.qwen_instruct_input.setText(settings.get("description", ""))
            elif mode == "voice_design":
                self.qwen_instruct_label.setText("Voice description")
                self.qwen_instruct_input.setPlaceholderText(
                    "e.g. a calm, low male voice with a slight rasp, unhurried and warm")
                self.qwen_instruct_input.setText(settings.get("description", ""))
            else:
                self.qwen_instruct_label.setText("Style")
                self.qwen_instruct_input.setPlaceholderText(
                    "Optional, e.g. slightly faster and cheerful" if voxcpm else
                    "Optional, e.g. excited and upbeat, or whisper softly")
                self.qwen_instruct_input.setText(settings.get("style", ""))
            self.qwen_speaker_label.setText("Voice" if mode == "preset" else "Speaker")
            for widget in (self.qwen_speaker_label, self.qwen_speaker_combo):
                widget.setVisible(mode in ("custom_voice", "preset"))
            for widget in (self.qwen_instruct_label, self.qwen_instruct_input):
                widget.setVisible(mode in ("custom_voice", "voice_design") or (voxcpm and mode == "base"))
            for widget in (self.qwen_transcript_label, self.qwen_transcript_input):
                widget.setVisible(mode == "base")
            self.design_attributes_button.setVisible(omnivoice and mode == "voice_design")
            conversation = mode == "conversation"
            for widget in (self.cast_title, self.cast_label, self.cast_button):
                widget.setVisible(conversation)
            if conversation:
                self.refresh_cast_label()
            self.qwen_transcript_input.setPlaceholderText(
                "What is said in the reference clip (required by OmniVoice)" if omnivoice else
                "What is said in the reference clip (optional, improves likeness)")
        else:
            self.design_attributes_button.setVisible(False)
            for widget in (self.cast_title, self.cast_label, self.cast_button):
                widget.setVisible(False)
        self.text_input.setPlaceholderText(
            vibevoice_engine.SCRIPT_HINT if qwen is not None and qwen.mode == "conversation" else
            "Enter text to synthesize, or open a document. Long text is split where a reader "
            "would pause and stitched back together.")
        self.refresh_voice_chip()
        if self.isVisible():
            self.update_minimum_size()

    def refresh_voice_chip(self):
        qwen = self.active_qwen_model()
        reference = self.ref_audio_path_label.toolTip()
        if qwen is not None and qwen.mode == "conversation":
            speakers = self.script_speakers()
            text = f"Cast: {len(speakers)} voice{'s' if len(speakers) != 1 else ''}"
            tip = "Each speaker in the script has their own voice. Change them with Cast\u2026 in Delivery."
        elif qwen is not None and qwen.mode in ("custom_voice", "preset"):
            name = self.qwen_speaker_combo.currentText().split(" (")[0]
            text = f"Preset: {name or 'speaker'}"
            tip = "A built-in voice. Reference clips aren't used by this model."
        elif qwen is not None and qwen.mode == "voice_design" and getattr(qwen, "locked_anchor", None):
            name = getattr(self, "locked_voice_name", None) or "kept voice"
            text, tip = f"Designed: {name}", ("Locked to a voice you kept: every section uses it. Change "
                                              "the description to design a new voice.")
        elif qwen is not None and qwen.mode == "voice_design":
            text, tip = "Designed voice", "Described in the Delivery card below. Preview, then Keep this voice to lock it."
        elif reference:
            saved = self.voice_library.find_clip(reference)
            text, tip = (saved.name if saved else os.path.basename(reference)), reference
        else:
            text = "Default voice"
            tip = "The model's built-in voice. Pick a reference clip on the Voice page to clone a voice."
            if qwen is not None:
                text, tip = "No clip selected", "This cloning model needs a reference clip from the Voice page."
        self.voice_chip.setText(text)
        self.voice_chip.setToolTip(tip)

    def prepare_qwen_generation(self):
        """Copy the Qwen card into the model; returns an error message or None."""
        qwen = self.active_qwen_model()
        if qwen is None:
            return None
        instruct = self.qwen_instruct_input.text().strip()
        if qwen.mode == "voice_design" and not instruct:
            return "Describe the voice you want (Voice description, in the Delivery card) first."
        if qwen.mode == "base" and not self.ref_audio_path_label.toolTip():
            return "Choose a reference clip on the Voice page; this cloning model needs one."
        if isinstance(qwen, vibevoice_engine.VibeVoiceModel):
            speakers = self.script_speakers()
            if len(speakers) > vibevoice_engine.MAX_SPEAKERS:
                return (f"VibeVoice handles up to {vibevoice_engine.MAX_SPEAKERS} speakers; this script has "
                        f"{len(speakers)}: {', '.join(speakers)}.")
            qwen.cast = self.current_cast(speakers)
            qwen.watermark = self.qwen_watermark_checkbox.isChecked()
            qwen.begin_run()
            self.vibevoice_settings["watermark"] = qwen.watermark
            self.app_settings["vibevoice"] = self.vibevoice_settings
            return None
        if isinstance(qwen, omnivoice_engine.OmniVoiceModel):
            if qwen.mode == "base" and not self.qwen_transcript_input.text().strip():
                return ("OmniVoice needs the Clip transcript: type exactly what is said in the "
                        "reference clip. Recordings made with Record\u2026 fill it in for you.")
            if qwen.mode == "voice_design":
                problem = omnivoice_engine.check_description(instruct)
                if problem:
                    return problem
        qwen.speaker = self.qwen_speaker_combo.currentData() or qwen.speaker
        qwen.watermark = self.qwen_watermark_checkbox.isChecked()
        if isinstance(qwen, kokoro_engine.KokoroModel):
            language = self.language_combo.currentData() or "en"
            self.kokoro_settings.setdefault("voice_by_language", {})[language] = qwen.speaker
            self.kokoro_settings["watermark"] = qwen.watermark
            self.app_settings["kokoro"] = self.kokoro_settings
            return None
        qwen.instruct = instruct
        qwen.ref_text = self.qwen_transcript_input.text().strip() if qwen.mode == "base" else ""
        if getattr(qwen, "locked_anchor", None) and instruct != getattr(self, "locked_description", instruct):
            qwen.locked_anchor = None  # the description changed: design a new voice
            self.locked_voice_name = None
            self.set_status_message("Status: Description changed, so a new voice will be designed.")
        if hasattr(qwen, "begin_run"):
            qwen.begin_run()
        if qwen.mode == "voice_design":
            # The description this run designs from, so "Keep this voice" saves the
            # wording that made the voice even if the box is edited afterwards.
            self.anchor_description = instruct
        key = "description" if qwen.mode == "voice_design" else "style"
        settings = self.engine_settings(qwen)
        settings.update({key: instruct, "watermark": qwen.watermark})
        if isinstance(qwen, DUAL_MODE_TYPES):
            self.app_settings[qwen.backend] = settings
        else:
            settings["speaker"] = qwen.speaker
            self.app_settings["qwen"] = settings
        return None

    # --- Conversations (VibeVoice) ---

    def script_speakers(self):
        return documents.script_speakers(documents.parse_script(self.text_input.toPlainText()))

    def current_cast(self, speakers):
        model = self.active_qwen_model()
        samples = getattr(model, "sample_paths", {}) or {}
        return vibevoice_engine.resolve_cast(speakers, self.vibevoice_settings.get("cast", {}), samples)

    def refresh_cast_label(self):
        model = self.active_qwen_model()
        if not isinstance(model, vibevoice_engine.VibeVoiceModel):
            return
        speakers = self.script_speakers()
        cast = self.current_cast(speakers)
        def cast_name(path):
            voice = self.voice_library.find_clip(path)
            return voice.name if voice else vibevoice_engine.voice_name(path, model.sample_paths)

        parts = [f"{speaker} \u2192 {cast_name(cast[speaker])}" for speaker in speakers if speaker in cast]
        text = " \u00b7 ".join(parts) if parts else "Write lines like \u201cLinda: Hello.\u201d"
        if len(speakers) > vibevoice_engine.MAX_SPEAKERS:
            text = f"{len(speakers)} speakers: VibeVoice handles up to {vibevoice_engine.MAX_SPEAKERS}"
        self.cast_label.setText(text)
        self.cast_label.setToolTip("\n".join(f"{speaker}: {cast.get(speaker, '')}" for speaker in speakers))
        self.refresh_voice_chip()

    def edit_cast(self):
        model = self.active_qwen_model()
        if not isinstance(model, vibevoice_engine.VibeVoiceModel):
            return
        speakers = self.script_speakers()[:vibevoice_engine.MAX_SPEAKERS]
        if not speakers:
            QMessageBox.information(self, "Cast", "Write the script first, one speaker per line, e.g.\n\n"
                                    + vibevoice_engine.SCRIPT_HINT.split("\n", 1)[1])
            return
        self.voice_library.import_recordings()
        recordings = [(voice.name, self.voice_library.clip_path(voice)) for voice in self.voice_library.clip_voices()]
        reference = self.ref_audio_path_label.toolTip()
        if reference and reference not in {path for _name, path in recordings}:
            recordings.insert(0, (os.path.basename(reference), reference))
        dialog = CastDialog(speakers, self.current_cast(speakers), model.sample_paths, recordings, self)
        if dialog_accepted(dialog.exec()):
            chosen = dict(self.vibevoice_settings.get("cast", {}))
            chosen.update(dialog.result_cast())
            self.vibevoice_settings["cast"] = chosen
            self.app_settings["vibevoice"] = self.vibevoice_settings
            self.refresh_cast_label()

    def _build_engine_rows(self):
        # Qwen controls share Delivery's first row with the Chatterbox-only sliders,
        # so switching engines never changes the window's minimum height.
        self.qwen_row = QWidget()
        qwen_row_layout = QHBoxLayout(self.qwen_row)
        qwen_row_layout.setContentsMargins(0, 0, 0, 0)
        qwen_row_layout.setSpacing(10)
        qwen_settings = self.app_settings.get("qwen", {})
        self.qwen_speaker_combo = QComboBox()
        # Kokoro's voice names are long ("Heart (US English, female)"); the list opens wide
        # anyway, so the box itself stays compact instead of widening the window.
        self.qwen_speaker_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.qwen_speaker_combo.setMinimumContentsLength(14)
        self.qwen_speaker_combo.view().setMinimumWidth(260)
        self.qwen_speaker_combo.setToolTip("Built-in Qwen speaker.")
        self.qwen_speaker_combo.currentIndexChanged.connect(lambda _i: self.refresh_voice_chip())
        self.qwen_speaker_label = QLabel("Speaker")
        qwen_row_layout.addWidget(self.qwen_speaker_label)
        qwen_row_layout.addWidget(self.qwen_speaker_combo)
        self.qwen_instruct_input = QLineEdit()
        self.qwen_instruct_label = QLabel("Style")
        qwen_row_layout.addWidget(self.qwen_instruct_label)
        qwen_row_layout.addWidget(self.qwen_instruct_input, 1)
        self.design_attributes_button = self._link(QPushButton("Attributes\u2026"))
        self.design_attributes_button.setToolTip("Pick the voice's gender, age, pitch, accent and more.")
        attributes_menu = QMenu(self)
        # Keep Python references: PySide can otherwise free submenus made by addMenu(title).
        self.design_attribute_menus = [attributes_menu]
        for group, items in omnivoice_engine.DESIGN_ATTRIBUTES.items():
            submenu = QMenu(group, attributes_menu)
            attributes_menu.addMenu(submenu)
            self.design_attribute_menus.append(submenu)
            for item in items:
                action = submenu.addAction(item)
                action.triggered.connect(lambda _checked=False, item=item: self.qwen_instruct_input.setText(
                    omnivoice_engine.set_attribute(self.qwen_instruct_input.text(), item)))
        attributes_menu.addSeparator()
        attributes_menu.addAction("Clear").triggered.connect(lambda: self.qwen_instruct_input.clear())
        self.design_attributes_button.setMenu(attributes_menu)
        self.design_attributes_button.setVisible(False)
        qwen_row_layout.addWidget(self.design_attributes_button)
        self.qwen_transcript_label = QLabel("Clip transcript")
        self.qwen_transcript_input = QLineEdit()
        self.qwen_transcript_input.setPlaceholderText(
            "What is said in the reference clip (optional, improves likeness)")
        self.qwen_transcript_input.setToolTip(
            "With a transcript, Qwen and VoxCPM clone more closely. It must match what is said in "
            "the clip: a wrong transcript can garble VoxCPM's output. Recordings made with "
            "Record... fill this in with the passage you read; edit it if you said something different.")
        self.qwen_transcript_input.editingFinished.connect(self.save_reference_transcript)
        qwen_row_layout.addWidget(self.qwen_transcript_label)
        qwen_row_layout.addWidget(self.qwen_transcript_input, 1)
        self.cast_label = QLabel()
        self.cast_label.setObjectName("Muted")
        self.cast_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cast_button = QPushButton("Cast\u2026")
        self.cast_button.setToolTip("Choose a voice for each speaker in the script.")
        self.cast_button.clicked.connect(self.edit_cast)
        self.cast_title = QLabel("Speakers")
        for widget in (self.cast_title, self.cast_label, self.cast_button):
            widget.setVisible(False)
        qwen_row_layout.addWidget(self.cast_title)
        qwen_row_layout.addWidget(self.cast_label, 1)
        qwen_row_layout.addWidget(self.cast_button)
        self.qwen_watermark_checkbox = QCheckBox("Add AI watermark")
        self.qwen_watermark_checkbox.setChecked(bool(qwen_settings.get("watermark", True)))
        self.qwen_watermark_checkbox.setToolTip(
            "Qwen and Kokoro don't watermark their audio. When ticked, the same inaudible Perth watermark "
            "Chatterbox uses is added, so output from every engine is marked the same way.")
        self.qwen_settings = qwen_settings
        self.kokoro_settings = self.app_settings.get("kokoro", {})
        self.voxcpm_settings = self.app_settings.get("voxcpm", {})
        self.omnivoice_settings = self.app_settings.get("omnivoice", {})
        self.vibevoice_settings = self.app_settings.get("vibevoice", {})
        self.qwen_row.setVisible(False)
        self.qwen_watermark_checkbox.setVisible(False)
