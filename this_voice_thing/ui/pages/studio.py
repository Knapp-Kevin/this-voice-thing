"""The Studio page: make a voice once (clone, design or remix), refine it, and save it
frozen to the library, so every later render uses exactly that voice."""

import os
import shutil
import tempfile

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabBar,
    QWidget,
)

from this_voice_thing.core import audio_effects, model_registry, voice_library, voice_studio
from this_voice_thing.engines import omnivoice as omnivoice_engine
from this_voice_thing.ui.common import DUAL_MODE_BACKENDS, languages_for_backend
from this_voice_thing.ui.dialogs.voices import VoiceDetailsDialog
from this_voice_thing.ui.threads import TaskThread
from this_voice_thing.ui.widgets import dialog_accepted

MODE_TITLES = {"clone": "Clone", "design": "Design", "remix": "Remix"}
MODE_HINTS = {
    "clone": "Start from a recording or file. Use it as it is, or let a cloning model re-read a "
             "passage in that voice for a cleaner take.",
    "design": "Describe a voice. Each take is a different voice from the same description: "
              "keep the one you like.",
    "remix": "Start from a voice and change it with words, e.g. older, slower and warmer "
             "(VoxCPM2's styled cloning).",
}
ACTION_LABELS = {"clone": "Clone", "design": "Design", "remix": "Remix"}


class StudioPage:
    """The voice Studio. Mixed into ChatterboxApp."""

    # ---------- layout ----------

    def _build_studio_page(self):
        studio_page, studio_layout = self._make_page(
            "Studio", "Make a voice once, then use it everywhere. Saved voices sound the same in every render.")
        self.studio_dir = tempfile.mkdtemp(prefix="tvt_studio_")
        self.studio_takes = []
        self.studio_source = None
        self.studio_busy = False
        self.pending_studio = None
        self.studio_player = QMediaPlayer(self)
        self.studio_audio_output = QAudioOutput(self)
        self.studio_player.setAudioOutput(self.studio_audio_output)
        self.studio_player.playbackStateChanged.connect(self._studio_playback_changed)
        studio_layout.addWidget(self._build_studio_start_card())
        studio_layout.addWidget(self._build_studio_takes_card(), 1)
        studio_layout.addWidget(self._build_studio_refine_card())
        self.pages.addWidget(studio_page)
        self.set_studio_mode("design")

    def _build_studio_start_card(self):
        card, layout = self._make_card()
        header = QHBoxLayout()
        title = QLabel("Start")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        self.studio_mode_tabs = QTabBar()
        self.studio_mode_tabs.setObjectName("CapabilityTabs")
        self.studio_mode_tabs.setDrawBase(False)
        self.studio_mode_tabs.setExpanding(False)
        self.studio_mode_tabs.setUsesScrollButtons(False)
        self.studio_mode_tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        for mode in ("clone", "design", "remix"):
            self.studio_mode_tabs.setTabData(self.studio_mode_tabs.addTab(MODE_TITLES[mode]), mode)
        self.studio_mode_tabs.currentChanged.connect(
            lambda index: self.set_studio_mode(self.studio_mode_tabs.tabData(index)))
        header.addWidget(self.studio_mode_tabs)
        header.addStretch(1)
        layout.addLayout(header)
        self.studio_mode_hint = QLabel()
        self.studio_mode_hint.setObjectName("Muted")
        self.studio_mode_hint.setWordWrap(True)
        self.studio_mode_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.studio_mode_hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        # Source clip (clone and remix)
        self.studio_source_title = QLabel("Clip")
        grid.addWidget(self.studio_source_title, 0, 0)
        source_row = QHBoxLayout()
        self.studio_source_label = QLabel("No clip yet.")
        self.studio_source_label.setObjectName("Muted")
        self.studio_source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.studio_source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_row.addWidget(self.studio_source_label, 1)
        record = QPushButton("Record...")
        record.setToolTip("Record yourself reading a passage (about 15 s in a quiet room).")
        record.clicked.connect(lambda: self.open_recording_dialog(then=self.studio_set_source))
        source_row.addWidget(record)
        open_file = QPushButton("Open file...")
        open_file.clicked.connect(self.studio_open_source_file)
        source_row.addWidget(open_file)
        self.studio_library_button = QPushButton("Library")
        self.studio_library_button.setToolTip("Start from a voice already in your library.")
        self.studio_library_menu = QMenu(self)
        self.studio_library_menu.aboutToShow.connect(self._fill_studio_library_menu)
        self.studio_library_button.setMenu(self.studio_library_menu)
        source_row.addWidget(self.studio_library_button)
        self.studio_source_row = QWidget()
        self.studio_source_row.setLayout(source_row)
        source_row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.studio_source_row, 0, 1)
        self.studio_transcript_title = QLabel("Says")
        grid.addWidget(self.studio_transcript_title, 1, 0)
        transcript_row = QHBoxLayout()
        transcript_row.setContentsMargins(0, 0, 0, 0)
        self.studio_transcript_input = QLineEdit()
        self.studio_transcript_input.setPlaceholderText("What the clip says (cloning models match it more closely)")
        self.studio_transcript_input.editingFinished.connect(self._studio_source_transcript_edited)
        transcript_row.addWidget(self.studio_transcript_input, 1)
        transcribe = self._link(QPushButton("Transcribe"))
        transcribe.setToolTip("Fill this in with Whisper.")
        transcribe.clicked.connect(self.studio_transcribe_source)
        transcript_row.addWidget(transcribe)
        self.studio_transcript_row = QWidget()
        self.studio_transcript_row.setLayout(transcript_row)
        grid.addWidget(self.studio_transcript_row, 1, 1)
        # Description (design and remix)
        self.studio_description_title = QLabel("Description")
        grid.addWidget(self.studio_description_title, 2, 0)
        description_row = QHBoxLayout()
        description_row.setContentsMargins(0, 0, 0, 0)
        self.studio_description_input = QLineEdit()
        description_row.addWidget(self.studio_description_input, 1)
        self.studio_attributes_button = self._link(QPushButton("Attributes…"))
        self.studio_attributes_button.setToolTip("Pick OmniVoice's gender, age, pitch, accent and more.")
        attributes_menu = QMenu(self)
        self.studio_attribute_menus = [attributes_menu]  # PySide frees submenus without a reference
        for group, items in omnivoice_engine.DESIGN_ATTRIBUTES.items():
            submenu = QMenu(group, attributes_menu)
            attributes_menu.addMenu(submenu)
            self.studio_attribute_menus.append(submenu)
            for item in items:
                submenu.addAction(item).triggered.connect(
                    lambda _checked=False, item=item: self.studio_description_input.setText(
                        omnivoice_engine.set_attribute(self.studio_description_input.text(), item)))
        attributes_menu.addSeparator()
        attributes_menu.addAction("Clear").triggered.connect(self.studio_description_input.clear)
        self.studio_attributes_button.setMenu(attributes_menu)
        description_row.addWidget(self.studio_attributes_button)
        self.studio_description_row = QWidget()
        self.studio_description_row.setLayout(description_row)
        grid.addWidget(self.studio_description_row, 2, 1)
        # Engine, language, number of takes, action
        grid.addWidget(QLabel("Model"), 3, 0)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        self.studio_engine_combo = QComboBox()
        self.studio_engine_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.studio_engine_combo.setMinimumContentsLength(9)
        self.studio_engine_combo.currentIndexChanged.connect(lambda _index: self._studio_engine_changed())
        action_row.addWidget(self.studio_engine_combo, 1)
        self.studio_language_combo = QComboBox()
        self.studio_language_combo.setToolTip("Language of the passage the takes read.")
        self.studio_language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.studio_language_combo.setMinimumContentsLength(5)
        action_row.addWidget(self.studio_language_combo)
        action_row.addWidget(QLabel("Takes"))
        self.studio_count_spin = QSpinBox()
        self.studio_count_spin.setRange(1, voice_studio.MAX_CANDIDATES)
        self.studio_count_spin.setValue(3)
        self.studio_count_spin.setToolTip("How many takes to make at once, to choose between.")
        action_row.addWidget(self.studio_count_spin)
        self.studio_action_button = self._accent(QPushButton("Design"))
        self.studio_action_button.clicked.connect(self.studio_make_takes)
        action_row.addWidget(self.studio_action_button)
        grid.addLayout(action_row, 3, 1)
        # Chatterbox delivery, for clone takes
        self.studio_delivery_title = QLabel("Delivery")
        grid.addWidget(self.studio_delivery_title, 4, 0)
        delivery_row = QHBoxLayout()
        delivery_row.setContentsMargins(0, 0, 0, 0)
        delivery_row.addWidget(QLabel("Expressiveness"))
        self.studio_exaggeration = self._create_slider(0.25, 2.0, 0.05, 0.5)
        delivery_row.addWidget(self.studio_exaggeration, 1)
        delivery_row.addWidget(QLabel("Pacing"))
        self.studio_cfg = self._create_slider(0.2, 1.0, 0.05, 0.5)
        delivery_row.addWidget(self.studio_cfg, 1)
        self.studio_delivery_row = QWidget()
        self.studio_delivery_row.setLayout(delivery_row)
        self.studio_delivery_row.setToolTip("How Chatterbox reads the passage in the cloned voice.")
        grid.addWidget(self.studio_delivery_row, 4, 1)
        layout.addLayout(grid)
        self.studio_progress = QProgressBar()
        self.studio_progress.setRange(0, 0)
        self.studio_progress.setFixedHeight(6)
        self.studio_progress.setTextVisible(False)
        self.studio_progress.setVisible(False)
        layout.addWidget(self.studio_progress)
        return card

    def _build_studio_takes_card(self):
        card, layout = self._make_card()
        header = QHBoxLayout()
        title = QLabel("Takes")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        hint = QLabel("Pick the one you like; double-click to listen.")
        hint.setObjectName("Muted")
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(hint, 1)
        layout.addLayout(header)
        self.studio_take_list = QListWidget()
        self.studio_take_list.setMinimumHeight(70)
        self.studio_take_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.studio_take_list.currentRowChanged.connect(lambda _row: self._studio_take_selected())
        self.studio_take_list.itemDoubleClicked.connect(lambda _item: self.studio_play_take())
        layout.addWidget(self.studio_take_list, 1)
        take_row = QHBoxLayout()
        self.studio_play_button = QPushButton("Play")
        self.studio_play_button.clicked.connect(self.studio_play_take)
        take_row.addWidget(self.studio_play_button)
        self.studio_remove_button = QPushButton("Remove")
        self.studio_remove_button.clicked.connect(self.studio_remove_take)
        take_row.addWidget(self.studio_remove_button)
        take_row.addWidget(QLabel("Says"))
        self.studio_take_text = QLineEdit()
        self.studio_take_text.setToolTip("The exact words of this take. It's saved with the voice; cloning "
                                         "models use it to match the voice closely.")
        self.studio_take_text.editingFinished.connect(self._studio_take_text_edited)
        take_row.addWidget(self.studio_take_text, 1)
        layout.addLayout(take_row)
        test_row = QHBoxLayout()
        test_row.addWidget(QLabel("Test line"))
        self.studio_test_input = QLineEdit(voice_studio.TEST_LINE)
        test_row.addWidget(self.studio_test_input, 1)
        self.studio_try_button = QPushButton("Try it")
        self.studio_try_button.setToolTip("Hear the selected take's voice say the test line.")
        self.studio_try_button.clicked.connect(self.studio_try_take)
        test_row.addWidget(self.studio_try_button)
        layout.addLayout(test_row)
        return card

    def _build_studio_refine_card(self):
        card, layout = self._make_card("Refine and save")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("Keep"), 0, 0)
        keep_row = QHBoxLayout()
        keep_row.setContentsMargins(0, 0, 0, 0)
        self.studio_start_spin = QDoubleSpinBox()
        self.studio_end_spin = QDoubleSpinBox()
        for spin in (self.studio_start_spin, self.studio_end_spin):
            spin.setDecimals(1)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
            spin.setRange(0.0, 600.0)
        self.studio_end_spin.setSpecialValueText("end")
        keep_row.addWidget(self.studio_start_spin)
        keep_row.addWidget(QLabel("to"))
        keep_row.addWidget(self.studio_end_spin)
        keep_row.addStretch(1)
        grid.addLayout(keep_row, 0, 1)
        checks = QHBoxLayout()
        checks.setContentsMargins(0, 0, 0, 0)
        self.studio_trim_check = QCheckBox("Trim silence")
        self.studio_level_check = QCheckBox("Even volume")
        checks.addWidget(self.studio_trim_check)
        checks.addWidget(self.studio_level_check)
        checks.addStretch(1)
        grid.addLayout(checks, 0, 2, 1, 2)
        grid.addWidget(QLabel("Pitch"), 1, 0)
        self.studio_pitch = self._create_slider(*audio_effects.PITCH_RANGE, 0.5, 0.0, "{:+.1f} st")
        self.studio_pitch.setToolTip("Raise or lower the voice, keeping its character (semitones).")
        grid.addWidget(self.studio_pitch, 1, 1)
        grid.addWidget(QLabel("Speed"), 1, 2)
        self.studio_speed = self._create_slider(*audio_effects.SPEED_RANGE, 0.05, 1.0, "{:.2f}x")
        self.studio_speed.setToolTip("Make the voice speak faster or slower.")
        grid.addWidget(self.studio_speed, 1, 3)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self.studio_apply_button = QPushButton("Apply to take")
        self.studio_apply_button.setToolTip("Make a new take from the selected one with these edits, to listen "
                                            "before saving.")
        self.studio_apply_button.clicked.connect(self.studio_apply_edits)
        actions.addWidget(self.studio_apply_button)
        reset = self._link(QPushButton("Reset"))
        reset.clicked.connect(self.studio_reset_edits)
        actions.addWidget(reset)
        actions.addStretch(1)
        self.studio_save_button = self._accent(QPushButton("Save voice..."))
        self.studio_save_button.setToolTip("Save the selected take to the voice library, frozen: it will sound "
                                           "the same every time you use it.")
        self.studio_save_button.clicked.connect(self.studio_save_voice)
        actions.addWidget(self.studio_save_button)
        layout.addLayout(actions)
        return card

    # ---------- mode and engines ----------

    def studio_mode(self):
        return self.studio_mode_tabs.tabData(self.studio_mode_tabs.currentIndex())

    def set_studio_mode(self, mode):
        index = next(i for i in range(self.studio_mode_tabs.count()) if self.studio_mode_tabs.tabData(i) == mode)
        if self.studio_mode_tabs.currentIndex() != index:
            self.studio_mode_tabs.setCurrentIndex(index)  # comes back here through currentChanged
            return
        self.studio_mode_hint.setText(MODE_HINTS[mode])
        uses_source = mode in ("clone", "remix")
        for widget in (self.studio_source_title, self.studio_source_row,
                       self.studio_transcript_title, self.studio_transcript_row):
            widget.setVisible(uses_source)
        for widget in (self.studio_description_title, self.studio_description_row):
            widget.setVisible(mode in ("design", "remix"))
        self.studio_description_title.setText("Change it" if mode == "remix" else "Description")
        self.studio_description_input.setPlaceholderText(
            "e.g. older, slower and warmer, a little hoarse" if mode == "remix" else
            "e.g. a gruff old harbor master, low raspy voice, slow and deliberate")
        self.studio_action_button.setText(ACTION_LABELS[mode])
        self._fill_studio_engines()

    def studio_entries(self, mode=None):
        """Model entries that can do the mode: cloning, designing, or styled cloning (VoxCPM)."""
        mode = mode or self.studio_mode()
        entries = []
        for entry in self.get_visible_model_entries():
            capability = model_registry.capability_for(entry)
            if mode == "design" and capability == "design":
                entries.append(entry)
            elif mode == "clone" and capability == "clone":
                entries.append(entry)
            elif mode == "remix" and capability == "clone" and entry.get("backend") == "voxcpm":
                entries.append(entry)
        return entries

    def _fill_studio_engines(self):
        entries = self.studio_entries()
        previous = self.app_settings.get("studio_engine", {}).get(self.studio_mode())
        self.studio_engine_combo.blockSignals(True)
        self.studio_engine_combo.clear()
        for entry in entries:
            installed = self.engine_installed(entry)
            self.studio_engine_combo.addItem(entry["label"] + ("" if installed else " (installs on first use)"),
                                             self.entry_key(entry))
        loaded = self.loaded_entry_key() if self.model is not None else None
        keys = [self.entry_key(entry) for entry in entries]
        pick = previous if previous in keys else loaded if loaded in keys else (keys[0] if keys else None)
        if pick is not None:
            self.studio_engine_combo.setCurrentIndex(keys.index(pick))
        self.studio_engine_combo.blockSignals(False)
        self.studio_action_button.setEnabled(bool(entries) and not self.studio_busy)
        if not entries:
            self.studio_engine_combo.addItem(
                "VoxCPM2 voice cloning (add it on the Model page)" if self.studio_mode() == "remix"
                else "No suitable model (add one on the Model page)")
            self.studio_engine_combo.setEnabled(False)
        else:
            self.studio_engine_combo.setEnabled(True)
        self._studio_engine_changed()

    def studio_entry(self):
        key = self.studio_engine_combo.currentData()
        return next((entry for entry in self.model_entries if self.entry_key(entry) == key), None) if key else None

    def _studio_engine_changed(self):
        entry = self.studio_entry()
        backend = entry.get("backend") if entry else ""
        if entry is not None:
            self.app_settings.setdefault("studio_engine", {})[self.studio_mode()] = self.entry_key(entry)
        self.studio_attributes_button.setVisible(self.studio_mode() == "design" and backend == "omnivoice")
        chatterbox = backend in ("multilingual", "legacy") or (entry is not None and backend not in (
            "qwen3", "voxcpm", "omnivoice", "kokoro", "vibevoice"))
        clone_takes = self.studio_mode() == "clone"
        for widget in (self.studio_delivery_title, self.studio_delivery_row):
            widget.setVisible(clone_takes and chatterbox)
        previous = self.studio_language_combo.currentData() or self.app_settings.get("studio_language", "en")
        self.studio_language_combo.blockSignals(True)
        self.studio_language_combo.clear()
        languages = languages_for_backend(backend) if entry is not None else {}
        for code, name in sorted((languages or {"en": "English"}).items(), key=lambda item: item[1]):
            self.studio_language_combo.addItem(name, code)
        index = self.studio_language_combo.findData(previous)
        if index < 0:
            index = self.studio_language_combo.findData("en")
        self.studio_language_combo.setCurrentIndex(max(0, index))
        self.studio_language_combo.blockSignals(False)

    # ---------- the source clip ----------

    def studio_set_source(self, path, transcript=None, name=None):
        if not path or not os.path.exists(path):
            return
        if transcript is None:
            transcript = voice_library.read_transcript(path)
        saved = self.voice_library.find_clip(path)
        label = name or (saved.name if saved else os.path.basename(path))
        self.studio_source = voice_studio.Take(path, transcript, label, "source",
                                               voice_library.clip_seconds(path))
        self.studio_source_label.setText(f"{label}  ·  {self.format_clock(self.studio_source.seconds)}")
        self.studio_source_label.setToolTip(path)
        self.studio_transcript_input.setText(transcript)
        self.studio_transcript_input.setCursorPosition(0)
        if self.studio_mode() == "design":
            self.set_studio_mode("clone")
        self._studio_add_takes([voice_studio.Take(path, transcript, "Source: " + label, "source",
                                                  self.studio_source.seconds)], select_first=True)
        self.sidebar.setCurrentRow(self.PAGE_STUDIO)
        self.set_status_message(f"Status: {label} is in the Studio. Use it as it is, or make takes from it.")

    def studio_open_source_file(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open a voice clip", self.last_reference_audio_dir, "Audio files (*.wav *.mp3 *.flac *.ogg)")
        if path:
            self.last_reference_audio_dir = os.path.dirname(path)
            self.studio_set_source(path)

    def _fill_studio_library_menu(self):
        self.studio_library_menu.clear()
        voices = sorted((voice for voice in self.voice_library.voices
                         if voice.has_clip and os.path.exists(self.voice_library.clip_path(voice))),
                        key=lambda voice: voice.created, reverse=True)
        if not voices:
            self.studio_library_menu.addAction("No voices with a clip yet").setEnabled(False)
        for voice in voices[:40]:
            self.studio_library_menu.addAction(voice.name).triggered.connect(
                lambda _checked=False, voice=voice: self.open_voice_in_studio(voice))

    def open_voice_in_studio(self, voice):
        path = self.voice_library.clip_path(voice)
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Studio", f"{voice.name} has no clip to start from.")
            return
        self.studio_set_source(path, name=voice.name)

    def _studio_source_transcript_edited(self):
        if self.studio_source is None:
            return
        self.studio_source.text = self.studio_transcript_input.text().strip()
        for take in self.studio_takes:
            if take.origin == "source" and take.path == self.studio_source.path:
                take.text = self.studio_source.text
        self._studio_take_selected()

    def studio_transcribe_source(self):
        if self.studio_source is None:
            QMessageBox.information(self, "Studio", "Record or open a clip first.")
            return
        path = self.studio_source.path

        def done(transcript, error):
            if error or transcript is None:
                self.set_status_message(f"Status: Couldn't transcribe the clip: {error}")
                return
            if self.studio_source is not None and self.studio_source.path == path:
                self.studio_transcript_input.setText(transcript.text)
                self._studio_source_transcript_edited()
                self.set_status_message("Status: Transcribed the clip. Check it, then make takes or save.")

        self.run_transcription(path, "auto", "transcribe", done, "Status: Transcribing the clip...")

    # ---------- takes ----------

    def _studio_add_takes(self, takes, select_first=False):
        first_row = self.studio_take_list.count()
        for take in takes:
            self.studio_takes.append(take)
            item = QListWidgetItem(self._studio_take_title(take))
            item.setToolTip(take.text)
            self.studio_take_list.addItem(item)
        if takes:
            self.studio_take_list.setCurrentRow(first_row if select_first or self.studio_take_list.currentRow() < 0
                                                else self.studio_take_list.currentRow())

    def _studio_take_title(self, take):
        kind = {"source": "original clip", "clone": "cloned", "design": "designed", "remix": "remixed",
                "refined": "edited"}.get(take.origin, take.origin)
        return f"{take.label}   ·   {kind}   ·   {self.format_clock(take.seconds)}"

    def studio_selected_take(self):
        row = self.studio_take_list.currentRow()
        return self.studio_takes[row] if 0 <= row < len(self.studio_takes) else None

    def _studio_take_selected(self):
        take = self.studio_selected_take()
        self.studio_take_text.setText(take.text if take else "")
        self.studio_take_text.setCursorPosition(0)  # show the start of the words
        self.studio_take_text.setEnabled(take is not None)
        for button in (self.studio_play_button, self.studio_remove_button, self.studio_try_button,
                       self.studio_apply_button, self.studio_save_button):
            button.setEnabled(take is not None and not (self.studio_busy and button is not self.studio_play_button))
        self.studio_end_spin.setMaximum(round(take.seconds, 1) if take else 600.0)

    def _studio_take_text_edited(self):
        take = self.studio_selected_take()
        if take is not None:
            take.text = self.studio_take_text.text().strip()
            self.studio_take_list.currentItem().setToolTip(take.text)

    def studio_remove_take(self):
        row = self.studio_take_list.currentRow()
        if 0 <= row < len(self.studio_takes):
            self.studio_takes.pop(row)
            self.studio_take_list.takeItem(row)
            self._studio_take_selected()

    def studio_play_take(self, path=None):
        if self.studio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and path is None:
            self.studio_player.stop()
            return
        take = self.studio_selected_take()
        path = path or (take.path if take else None)
        if path:
            self.studio_player.setSource(QUrl.fromLocalFile(path))
            self.studio_player.play()

    def _studio_playback_changed(self, state):
        self.studio_play_button.setText("Stop" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    # ---------- running models ----------

    def studio_with_model(self, entry, then):
        """Run `then` once `entry` is the loaded model, loading it first if needed."""
        if entry is None:
            QMessageBox.information(self, "Studio", "There's no model for this yet. Add one on the Model page.")
            return
        if self.model_busy():
            self.set_status_message("Status: Wait for the current load or generation to finish.")
            return
        if self.is_active_entry(entry):
            then()
            return
        if not self.engine_installed(entry):
            self.install_engine(entry)
            return
        self.pending_studio = (self.entry_key(entry), then)
        self.set_status_message(f"Status: Loading {entry['label']} for the Studio...")
        self.load_entry(entry)

    def run_pending_studio(self):
        pending, self.pending_studio = self.pending_studio, None
        if pending and self.model is not None and pending[0] == self.loaded_entry_key():
            QTimer.singleShot(0, pending[1])

    def _studio_set_busy(self, busy, status=None):
        self.studio_busy = busy
        self.studio_progress.setVisible(busy)
        self.studio_action_button.setEnabled(not busy and self.studio_engine_combo.isEnabled())
        for button in (self.generate_button, self.preview_button):
            button.setEnabled(not busy and self.model is not None)
        self._studio_take_selected()
        if status:
            self.set_status_message(status)

    def _studio_run(self, work, on_result, status):
        model = self.model
        self._studio_set_busy(True, status)
        thread = TaskThread(lambda: work(model), self)

        def finished(result, error):
            self._studio_set_busy(False)
            if error:
                self.set_status_message("Status: The Studio couldn't finish. See the Log page.")
                print(f"Studio: {error}")
                QMessageBox.warning(self, "Studio", error)
                return
            on_result(result)

        thread.done.connect(finished)
        self.studio_thread = thread
        thread.start()

    def _studio_delivery(self, model):
        if voice_studio.is_worker_model(model):
            return {}
        return {"exaggeration": self.studio_exaggeration.get_value(), "cfg_weight": self.studio_cfg.get_value(),
                "temperature": 0.8}

    def studio_make_takes(self):
        mode = self.studio_mode()
        entry = self.studio_entry()
        count = self.studio_count_spin.value()
        language = self.studio_language_combo.currentData() or "en"
        self.app_settings["studio_language"] = language
        description = self.studio_description_input.text().strip()
        source = self.studio_source
        if mode in ("design", "remix") and not description:
            QMessageBox.information(self, "Studio", "Describe the voice first." if mode == "design" else
                                    "Say how to change the voice, e.g. older, slower and warmer.")
            return
        if mode in ("clone", "remix") and source is None:
            QMessageBox.information(self, "Studio", "Record, open or pick a clip to start from first.")
            return
        if entry is not None and entry.get("backend") == "omnivoice":
            if mode == "design":
                problem = omnivoice_engine.check_description(description)
                if problem:
                    QMessageBox.information(self, "Studio", problem)
                    return
            elif not source.text:
                QMessageBox.information(self, "Studio", "OmniVoice needs the clip's words: fill in Says "
                                        "(Transcribe does it for you).")
                return
        directory = self.studio_dir
        passage = voice_studio.DESIGN_PASSAGE
        first = sum(1 for take in self.studio_takes if take.origin == mode) + 1

        def work(model):
            takes = []
            for number in range(first, first + count):
                if mode == "design":
                    wav, sr, _seed = voice_studio.design(model, description, passage, language)
                else:
                    wav, sr, _seed = voice_studio.speak(
                        model, passage, source.path, source.text,
                        style=description if mode == "remix" else "", language_id=language,
                        delivery=self._studio_delivery(model))
                path = voice_studio.save_take(directory, mode, wav, sr)
                takes.append(voice_studio.Take(path, passage, f"{MODE_TITLES[mode]} {number}", mode, len(wav) / sr))
            return takes

        def show(takes):
            self._studio_add_takes(takes, select_first=True)
            self.set_status_message(f"Status: {len(takes)} new take{'s' if len(takes) != 1 else ''}. "
                                    "Listen, pick one, then Save voice.")
            self.studio_play_take(takes[0].path)

        self.studio_with_model(entry, lambda: self._studio_run(
            work, show, f"Status: Making {count} {MODE_TITLES[mode].lower()} take{'s' if count != 1 else ''}..."))

    def studio_try_take(self):
        take = self.studio_selected_take()
        line = self.studio_test_input.text().strip()
        if take is None or not line:
            return
        mode = self.studio_mode()
        entry = self.studio_entry()
        if mode == "remix":
            # The remix is baked into the take; any cloning model can say the line in it.
            entry = self.studio_entry_for_clone() or entry
        language = self.studio_language_combo.currentData() or "en"
        directory = self.studio_dir

        def work(model):
            wav, sr, _seed = voice_studio.speak(model, line, take.path, take.text, language_id=language,
                                                delivery=self._studio_delivery(model))
            return voice_studio.save_take(directory, "try", wav, sr)

        def play(path):
            self.set_status_message(f"Status: {take.label} saying the test line.")
            self.studio_play_take(path)

        self.studio_with_model(entry, lambda: self._studio_run(work, play, f"Status: {take.label}: trying it..."))

    def studio_entry_for_clone(self):
        """The loaded model if it can clone, else the first cloning model (avoids reloads)."""
        if self.model is not None and getattr(self.model, "mode", "base") in ("base", "voice_design"):
            current = self.loaded_entry()
            if current is not None and model_registry.capability_for(current) in ("clone", "design"):
                return current
        entries = self.studio_entries("clone")
        return entries[0] if entries else None

    # ---------- refine and save ----------

    def studio_reset_edits(self):
        self.studio_start_spin.setValue(0.0)
        self.studio_end_spin.setValue(0.0)
        self.studio_trim_check.setChecked(False)
        self.studio_level_check.setChecked(False)
        self.studio_pitch.set_value(0.0)
        self.studio_speed.set_value(1.0)

    def studio_apply_edits(self):
        take = self.studio_selected_take()
        if take is None:
            return
        start, end = self.studio_start_spin.value(), self.studio_end_spin.value()
        settings = dict(start=start, end=end or None, trim=self.studio_trim_check.isChecked(),
                        level=self.studio_level_check.isChecked(), speed=self.studio_speed.get_value(),
                        semitones=self.studio_pitch.get_value())
        if not (start or end or settings["trim"] or settings["level"]
                or abs(settings["speed"] - 1.0) > 1e-3 or abs(settings["semitones"]) > 1e-3):
            self.set_status_message("Status: Nothing to apply: set a cut, trim, volume, pitch or speed first.")
            return
        directory = self.studio_dir

        def work(_model):
            wav, sr = voice_studio.load_audio(take.path)
            edited = voice_studio.process_clip(wav, sr, log=print, **settings)
            return voice_studio.Take(voice_studio.save_take(directory, "edited", edited, sr), take.text,
                                     take.label + " (edited)", "refined", len(edited) / sr)

        def show(new_take):
            self._studio_add_takes([new_take], select_first=False)
            self.studio_take_list.setCurrentRow(len(self.studio_takes) - 1)
            note = " The cut may have changed what it says: check Says." if start or end else ""
            self.set_status_message(f"Status: Made {new_take.label}.{note}")
            self.studio_play_take(new_take.path)

        self._studio_run(work, show, "Status: Applying edits...")

    def studio_save_voice(self):
        take = self.studio_selected_take()
        if take is None:
            return
        if not take.text:
            answer = QMessageBox.question(
                self, "Save voice", "This take has no words in Says. Cloning models match a voice more "
                "closely when they know what the clip says. Save it anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        mode = self.studio_mode()
        entry = self.studio_entry()
        language = self.studio_language_combo.currentData() or ""
        description = self.studio_description_input.text().strip()
        design_entry = entry if mode == "design" else self._voxcpm_design_entry() if mode == "remix" else None
        if take.origin == "source" or (mode == "clone" and design_entry is None):
            voice = voice_library.Voice(name=self.studio_source.label if take.origin == "source" and self.studio_source
                                        else "Cloned voice", kind="clip", language=language,
                                        origin="recorded" if take.origin == "source" else "cloned")
        else:
            backend_entry = design_entry or entry
            voice = voice_library.Voice(
                name="Designed voice" if mode == "design" else "Remixed voice", kind="design",
                backend=backend_entry["backend"], repo_id=backend_entry["repo_id"],
                mode="design" if backend_entry["backend"] in DUAL_MODE_BACKENDS else "",
                description=description, language=language,
                origin="designed" if mode == "design" else "remixed")
        dialog = VoiceDetailsDialog("Save voice", voice, self.voice_library, parent=self)
        if not dialog_accepted(dialog.exec()):
            return
        dialog.apply()
        path = self.voice_library.new_clip_path(voice.name)
        shutil.copyfile(take.path, path)
        voice_library.write_transcript(path, take.text)
        voice.clip = self.voice_library.to_stored(path)
        self.voice_library.add(voice)
        self.render_voice_tiles()
        self.set_status_message(f"Status: Saved {voice.name}. It's frozen: pick it in Voices or on Generate "
                                "and it sounds the same every time.")

    def _voxcpm_design_entry(self):
        """A remixed voice is spoken by VoxCPM's design entry, which clones from its clip."""
        return next((entry for entry in self.model_entries if entry.get("backend") == "voxcpm"
                     and model_registry.entry_mode(entry) == "design"), None)
