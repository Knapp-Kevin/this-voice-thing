"""Live Voice page: type text and send generated PCM directly to an audio device."""

import json
import os

import soundfile as sf

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QInputDialog,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
)

from this_voice_thing.core import live_routes, soundboard, voice_library
from this_voice_thing.core.live_voice import AudioFileSpeechSession, CachedSpeechSession, LiveSpeechSession
from this_voice_thing.core.microphone_audio import MicrophoneEffectsConfig
from this_voice_thing.engines import vibevoice as vibevoice_engine
from this_voice_thing.ui.global_hotkeys import GlobalHotkeyManager, HotkeyError, normalize_hotkey
from this_voice_thing.ui.live_audio import LiveAudioOutput, LiveSpeechThread
from this_voice_thing.ui.live_capture import LiveAudioInput
from this_voice_thing.ui import theme as ui_theme


MAX_LIVE_QUEUE_ITEMS = 25
MAX_LIVE_QUEUE_SECONDS = 600.0
APPROX_SPEECH_CHARS_PER_SECOND = 13.0


class LiveVoicePage:
    """Live Speak UI mixed into ChatterboxApp."""

    def _build_live_voice_page(self):
        self.live_voice_queue = []
        self.live_voice_history = []
        self.live_voice_busy = False
        self.live_speech_thread = None
        self.live_voice_current = None
        self.live_voice_stop_all_requested = False
        self.live_voice_last_error = ""
        self.live_last_generation_metrics = {}
        self.live_audio_devices = []
        self.live_audio_inputs = []
        self.soundboard_store = soundboard.SoundboardStore(self.script_dir)
        self.live_route_store = live_routes.RouteProfileStore(
            self.app_settings.setdefault("live_voice", {})
        )
        self.live_hotkeys = GlobalHotkeyManager(self)
        self.live_external_armed = False
        self.live_board_defaults_applied = False

        self.live_mic_input = LiveAudioInput(self)
        self.live_mic_input.frame_ready.connect(self.on_live_mic_frame)
        self.live_mic_input.failed.connect(self.on_live_mic_error)
        self.live_mic_input.stopped.connect(self.on_live_mic_stopped)

        self.live_audio_output = LiveAudioOutput(self)
        self.live_audio_output.failed.connect(self.on_live_audio_error)
        self.live_audio_output.drained.connect(self.on_live_audio_drained)
        self.live_audio_output.buffer_changed.connect(self.on_live_buffer_changed)
        self.live_monitor_output = LiveAudioOutput(self)
        self.live_monitor_output.failed.connect(self.on_live_monitor_error)
        self.live_monitor_output.drained.connect(self.on_live_audio_drained)
        self.live_monitor_output.buffer_changed.connect(
            lambda _milliseconds: self._refresh_live_stop_buttons()
        )

        page, layout = self._make_page(
            "Live Voice",
            "Type text or use a microphone, then route the audio directly to an output device.",
        )

        route_card, route_layout = self._make_card("Voice & route")
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice"))
        self.live_voice_name_label = QLabel("Default voice")
        self.live_voice_name_label.setObjectName("VoiceChip")
        voice_row.addWidget(self.live_voice_name_label)
        change_voice = self._link(QPushButton("Change"))
        live_voice_menu = self._build_voice_menu()
        live_voice_menu.aboutToHide.connect(self.refresh_live_voice_summary)
        change_voice.setMenu(live_voice_menu)
        voice_row.addWidget(change_voice)
        voice_row.addSpacing(14)
        voice_row.addWidget(QLabel("Model"))
        self.live_model_label = QLabel("No model loaded")
        self.live_model_label.setObjectName("Muted")
        voice_row.addWidget(self.live_model_label)
        voice_row.addStretch(1)
        self.live_mode_label = QLabel("Buffered")
        self.live_mode_label.setObjectName("Muted")
        voice_row.addWidget(self.live_mode_label)
        self.live_voice_origin_label = QLabel("Origin: default")
        self.live_voice_origin_label.setObjectName("Muted")
        voice_row.addWidget(self.live_voice_origin_label)
        self.live_provenance_label = QLabel("Provenance: idle")
        self.live_provenance_label.setObjectName("Muted")
        voice_row.addWidget(self.live_provenance_label)
        route_layout.addLayout(voice_row)

        favorite_voice_row = QHBoxLayout()
        favorite_voice_row.addWidget(QLabel("Quick voices"))
        self.live_favorite_voice_combo = QComboBox()
        favorite_voice_row.addWidget(self.live_favorite_voice_combo, 1)
        use_favorite_voice = QPushButton("Use")
        use_favorite_voice.clicked.connect(self.use_selected_favorite_voice)
        favorite_voice_row.addWidget(use_favorite_voice)
        favorite_current_voice = self._link(QPushButton("Favorite current"))
        favorite_current_voice.clicked.connect(self.favorite_current_live_voice)
        favorite_voice_row.addWidget(favorite_current_voice)
        remove_favorite_voice = self._link(QPushButton("Remove favorite"))
        remove_favorite_voice.clicked.connect(self.remove_selected_favorite_voice)
        favorite_voice_row.addWidget(remove_favorite_voice)
        route_layout.addLayout(favorite_voice_row)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Route"))
        self.live_route_profile_combo = QComboBox()
        for profile in self.live_route_store.profiles:
            self.live_route_profile_combo.addItem(profile.name, profile.id)
        profile_index = self.live_route_profile_combo.findData(self.live_route_store.active_id)
        self.live_route_profile_combo.setCurrentIndex(max(0, profile_index))
        self.live_route_profile_combo.currentIndexChanged.connect(self.on_live_route_profile_changed)
        profile_row.addWidget(self.live_route_profile_combo, 1)
        self.live_arm_button = QPushButton("Arm external route")
        self.live_arm_button.clicked.connect(self.toggle_live_external_arm)
        profile_row.addWidget(self.live_arm_button)
        self.live_route_test_button = QPushButton("Test route")
        self.live_route_test_button.clicked.connect(self.live_test_route)
        profile_row.addWidget(self.live_route_test_button)
        self.live_route_setup_button = QPushButton("Setup…")
        self.live_route_setup_button.clicked.connect(self.show_live_app_setup)
        profile_row.addWidget(self.live_route_setup_button)
        self.live_route_state = QLabel("LOCAL ONLY")
        self.live_route_state.setObjectName("Muted")
        profile_row.addWidget(self.live_route_state)
        route_layout.addLayout(profile_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Send voice to"))
        self.live_output_combo = QComboBox()
        self.live_output_combo.setMinimumWidth(320)
        self.live_output_combo.currentIndexChanged.connect(self.on_live_output_changed)
        output_row.addWidget(self.live_output_combo, 1)
        refresh = self._link(QPushButton("Refresh devices"))
        refresh.clicked.connect(self.refresh_live_audio_devices)
        output_row.addWidget(refresh)
        route_layout.addLayout(output_row)

        paired_row = QHBoxLayout()
        paired_row.addWidget(QLabel("Use as microphone"))
        self.live_paired_input_label = QLabel("Not applicable for local output")
        self.live_paired_input_label.setObjectName("Muted")
        self.live_paired_input_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        paired_row.addWidget(self.live_paired_input_label, 1)
        self.live_copy_mic_button = self._link(QPushButton("Copy microphone name"))
        self.live_copy_mic_button.clicked.connect(self.copy_live_paired_input_name)
        paired_row.addWidget(self.live_copy_mic_button)
        route_layout.addLayout(paired_row)

        monitor_row = QHBoxLayout()
        self.live_monitor_checkbox = QCheckBox("Also let me hear it through")
        self.live_monitor_checkbox.toggled.connect(self.on_live_monitor_toggled)
        monitor_row.addWidget(self.live_monitor_checkbox)
        self.live_monitor_combo = QComboBox()
        self.live_monitor_combo.setMinimumWidth(320)
        self.live_monitor_combo.currentIndexChanged.connect(self.on_live_monitor_changed)
        monitor_row.addWidget(self.live_monitor_combo, 1)
        self.live_monitor_status = QLabel("")
        self.live_monitor_status.setObjectName("Muted")
        monitor_row.addWidget(self.live_monitor_status)
        route_layout.addLayout(monitor_row)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source"))
        self.live_source_mode_combo = QComboBox()
        self.live_source_mode_combo.addItem("Live Speak", "tts")
        self.live_source_mode_combo.addItem("Mic Effects", "mic_effects")
        self.live_source_mode_combo.currentIndexChanged.connect(
            self.on_live_source_mode_changed
        )
        source_row.addWidget(self.live_source_mode_combo)
        source_row.addStretch(1)
        route_layout.addLayout(source_row)
        layout.addWidget(route_card)

        mic_card, mic_layout = self._make_card("Mic Effects")
        mic_settings = self.app_settings.setdefault("live_voice", {}).setdefault(
            "microphone", {}
        )
        mic_input_row = QHBoxLayout()
        mic_input_row.addWidget(QLabel("Microphone"))
        self.live_mic_input_combo = QComboBox()
        self.live_mic_input_combo.setMinimumWidth(320)
        self.live_mic_input_combo.currentIndexChanged.connect(
            self.on_live_mic_input_changed
        )
        mic_input_row.addWidget(self.live_mic_input_combo, 1)
        refresh_mics = self._link(QPushButton("Refresh devices"))
        refresh_mics.clicked.connect(self.refresh_live_audio_devices)
        mic_input_row.addWidget(refresh_mics)
        self.live_mic_start_button = self._accent(QPushButton("Start microphone"))
        self.live_mic_start_button.clicked.connect(self.toggle_live_microphone)
        mic_input_row.addWidget(self.live_mic_start_button)
        mic_layout.addLayout(mic_input_row)

        effects_row = QHBoxLayout()
        self.live_mic_effects_enabled = QCheckBox("Enable effects")
        self.live_mic_effects_enabled.setChecked(
            bool(mic_settings.get("effects_enabled", True))
        )
        self.live_mic_effects_enabled.toggled.connect(
            self.on_live_mic_effect_setting_changed
        )
        effects_row.addWidget(self.live_mic_effects_enabled)

        effects_row.addWidget(QLabel("Gain dB"))
        self.live_mic_gain = QDoubleSpinBox()
        self.live_mic_gain.setRange(-24.0, 24.0)
        self.live_mic_gain.setDecimals(1)
        self.live_mic_gain.setSingleStep(1.0)
        self.live_mic_gain.setValue(float(mic_settings.get("gain_db", 0.0)))
        self.live_mic_gain.valueChanged.connect(
            self.on_live_mic_effect_setting_changed
        )
        effects_row.addWidget(self.live_mic_gain)

        effects_row.addWidget(QLabel("Tone"))
        self.live_mic_tone = QDoubleSpinBox()
        self.live_mic_tone.setRange(-1.0, 1.0)
        self.live_mic_tone.setDecimals(2)
        self.live_mic_tone.setSingleStep(0.1)
        self.live_mic_tone.setValue(float(mic_settings.get("tone", 0.0)))
        self.live_mic_tone.setToolTip("-1 warmer/darker · +1 brighter")
        self.live_mic_tone.valueChanged.connect(
            self.on_live_mic_effect_setting_changed
        )
        effects_row.addWidget(self.live_mic_tone)

        self.live_mic_compressor = QCheckBox("Compressor")
        self.live_mic_compressor.setChecked(
            bool(mic_settings.get("compressor_enabled", True))
        )
        self.live_mic_compressor.toggled.connect(
            self.on_live_mic_effect_setting_changed
        )
        effects_row.addWidget(self.live_mic_compressor)

        effects_row.addStretch(1)
        self.live_mic_status_label = QLabel("Microphone stopped")
        self.live_mic_status_label.setObjectName("Muted")
        effects_row.addWidget(self.live_mic_status_label)
        mic_layout.addLayout(effects_row)

        layout.addWidget(mic_card)

        speak_card, speak_layout = self._make_card("Live Speak")
        self.live_text_input = QPlainTextEdit()
        self.live_text_input.setPlaceholderText(
            "Type what you want the selected voice to say. Ctrl+Enter sends it."
        )
        self.live_text_input.setMinimumHeight(105)
        speak_layout.addWidget(self.live_text_input)

        controls = QHBoxLayout()
        self.live_speak_button = self._accent(QPushButton("Speak"))
        self.live_speak_button.clicked.connect(self.live_submit)
        controls.addWidget(self.live_speak_button)
        self.live_stop_current_button = QPushButton("Stop current")
        self.live_stop_current_button.clicked.connect(self.live_stop_current)
        self.live_stop_current_button.setEnabled(False)
        controls.addWidget(self.live_stop_current_button)
        self.live_stop_all_button = QPushButton("Stop all")
        self.live_stop_all_button.clicked.connect(self.live_stop_all)
        self.live_stop_all_button.setEnabled(False)
        controls.addWidget(self.live_stop_all_button)
        diagnostics = self._link(QPushButton("Copy diagnostics"))
        diagnostics.clicked.connect(self.copy_live_diagnostics)
        controls.addWidget(diagnostics)
        controls.addStretch(1)
        self.live_status_label = QLabel("Ready")
        self.live_status_label.setObjectName("Muted")
        controls.addWidget(self.live_status_label)
        speak_layout.addLayout(controls)
        layout.addWidget(speak_card)

        queue_card, queue_layout = self._make_card("Queue")
        self.live_current_label = QLabel("Nothing speaking.")
        self.live_current_label.setObjectName("Muted")
        queue_layout.addWidget(self.live_current_label)
        self.live_queue_list = QListWidget()
        self.live_queue_list.setMinimumHeight(110)
        queue_layout.addWidget(self.live_queue_list)
        queue_actions = QHBoxLayout()
        repeat = self._link(QPushButton("Repeat last"))
        repeat.clicked.connect(self.live_repeat_last)
        queue_actions.addWidget(repeat)
        remove = self._link(QPushButton("Remove selected"))
        remove.clicked.connect(self.live_remove_selected_queue_item)
        queue_actions.addWidget(remove)
        move_up = self._link(QPushButton("Move up"))
        move_up.clicked.connect(lambda: self.live_move_selected_queue_item(-1))
        queue_actions.addWidget(move_up)
        move_down = self._link(QPushButton("Move down"))
        move_down.clicked.connect(lambda: self.live_move_selected_queue_item(1))
        queue_actions.addWidget(move_down)
        clear = self._link(QPushButton("Clear queued"))
        clear.clicked.connect(self.live_clear_queue)
        queue_actions.addWidget(clear)
        clear_history = self._link(QPushButton("Clear history"))
        clear_history.clicked.connect(self.live_clear_history)
        queue_actions.addWidget(clear_history)
        queue_actions.addStretch(1)
        self.live_queue_pressure_label = QLabel("0 queued")
        self.live_queue_pressure_label.setObjectName("Muted")
        queue_actions.addWidget(self.live_queue_pressure_label)
        queue_layout.addLayout(queue_actions)
        layout.addWidget(queue_card, 1)

        board_card, board_layout = self._make_card("Soundboard")
        board_picker_row = QHBoxLayout()
        board_picker_row.addWidget(QLabel("Board"))
        self.soundboard_board_combo = QComboBox()
        self.soundboard_board_combo.setMinimumWidth(220)
        self.soundboard_board_combo.currentIndexChanged.connect(self.on_soundboard_board_changed)
        board_picker_row.addWidget(self.soundboard_board_combo, 1)
        new_board = QPushButton("New…")
        new_board.clicked.connect(self.create_soundboard_board)
        board_picker_row.addWidget(new_board)
        rename_board = QPushButton("Rename…")
        rename_board.clicked.connect(self.rename_soundboard_board)
        board_picker_row.addWidget(rename_board)
        delete_board = self._link(QPushButton("Delete board"))
        delete_board.clicked.connect(self.delete_soundboard_board)
        board_picker_row.addWidget(delete_board)
        board_layout.addLayout(board_picker_row)

        defaults_row = QHBoxLayout()
        defaults_row.addWidget(QLabel("Board defaults"))
        self.soundboard_defaults_label = QLabel("Voice: none · Route: none")
        self.soundboard_defaults_label.setObjectName("Muted")
        defaults_row.addWidget(self.soundboard_defaults_label, 1)
        save_voice_default = QPushButton("Use current voice")
        save_voice_default.clicked.connect(self.set_soundboard_default_voice)
        defaults_row.addWidget(save_voice_default)
        clear_voice_default = self._link(QPushButton("Clear voice"))
        clear_voice_default.clicked.connect(self.clear_soundboard_default_voice)
        defaults_row.addWidget(clear_voice_default)
        save_route_default = QPushButton("Use current route")
        save_route_default.clicked.connect(self.set_soundboard_default_route)
        defaults_row.addWidget(save_route_default)
        clear_route_default = self._link(QPushButton("Clear route"))
        clear_route_default.clicked.connect(self.clear_soundboard_default_route)
        defaults_row.addWidget(clear_route_default)
        board_layout.addLayout(defaults_row)

        policy_row = QHBoxLayout()
        policy_row.addWidget(QLabel("Default pad behavior"))
        self.soundboard_board_policy_combo = QComboBox()
        self.soundboard_board_policy_combo.addItem("Queue", "queue")
        self.soundboard_board_policy_combo.addItem("Interrupt current", "interrupt")
        self.soundboard_board_policy_combo.addItem("Ignore if busy", "ignore")
        self.soundboard_board_policy_combo.currentIndexChanged.connect(
            self.on_soundboard_board_policy_changed
        )
        policy_row.addWidget(self.soundboard_board_policy_combo)
        policy_row.addStretch(1)
        board_layout.addLayout(policy_row)

        filter_row = QHBoxLayout()
        self.soundboard_search_input = QLineEdit()
        self.soundboard_search_input.setPlaceholderText("Search pads by name, phrase, or tag…")
        self.soundboard_search_input.textChanged.connect(lambda _text: self.refresh_soundboard())
        filter_row.addWidget(self.soundboard_search_input, 1)
        self.soundboard_favorites_only = QCheckBox("Favorites only")
        self.soundboard_favorites_only.toggled.connect(lambda _checked: self.refresh_soundboard())
        filter_row.addWidget(self.soundboard_favorites_only)
        board_layout.addLayout(filter_row)

        self.soundboard_list = QListWidget()
        self.soundboard_list.setMinimumHeight(130)
        self.soundboard_list.itemDoubleClicked.connect(lambda _item: self.trigger_selected_soundboard_pad())
        board_layout.addWidget(self.soundboard_list)
        board_actions = QHBoxLayout()
        save_pad = self._accent(QPushButton("Save text as pad"))
        save_pad.clicked.connect(self.save_soundboard_pad)
        board_actions.addWidget(save_pad)
        add_audio_pad = QPushButton("Add audio clip…")
        add_audio_pad.clicked.connect(self.add_soundboard_audio_pad)
        board_actions.addWidget(add_audio_pad)
        trigger_pad = QPushButton("Trigger")
        trigger_pad.clicked.connect(self.trigger_selected_soundboard_pad)
        board_actions.addWidget(trigger_pad)
        delete_pad = self._link(QPushButton("Delete"))
        delete_pad.clicked.connect(self.delete_selected_soundboard_pad)
        board_actions.addWidget(delete_pad)
        move_pad_up = self._link(QPushButton("Move up"))
        move_pad_up.clicked.connect(lambda: self.move_selected_soundboard_pad(-1))
        board_actions.addWidget(move_pad_up)
        move_pad_down = self._link(QPushButton("Move down"))
        move_pad_down.clicked.connect(lambda: self.move_selected_soundboard_pad(1))
        board_actions.addWidget(move_pad_down)
        set_hotkey = QPushButton("Set hotkey…")
        set_hotkey.clicked.connect(self.set_selected_soundboard_hotkey)
        board_actions.addWidget(set_hotkey)
        clear_hotkey = self._link(QPushButton("Clear hotkey"))
        clear_hotkey.clicked.connect(self.clear_selected_soundboard_hotkey)
        board_actions.addWidget(clear_hotkey)
        favorite_pad = self._link(QPushButton("Favorite"))
        favorite_pad.clicked.connect(self.toggle_selected_soundboard_favorite)
        board_actions.addWidget(favorite_pad)
        tags_pad = self._link(QPushButton("Tags…"))
        tags_pad.clicked.connect(self.edit_selected_soundboard_tags)
        board_actions.addWidget(tags_pad)
        behavior_pad = self._link(QPushButton("Behavior…"))
        behavior_pad.clicked.connect(self.set_selected_soundboard_behavior)
        board_actions.addWidget(behavior_pad)
        clear_cache = self._link(QPushButton("Clear cache"))
        clear_cache.clicked.connect(self.clear_soundboard_cache)
        board_actions.addWidget(clear_cache)
        board_actions.addStretch(1)
        self.soundboard_status_label = QLabel("Static TTS pads cache locally after their first successful generation.")
        self.soundboard_status_label.setObjectName("Muted")
        board_actions.addWidget(self.soundboard_status_label)
        board_layout.addLayout(board_actions)

        hotkey_row = QHBoxLayout()
        self.live_hotkeys_enabled_checkbox = QCheckBox("Enable global pad hotkeys")
        self.live_hotkeys_enabled_checkbox.setChecked(
            bool(self.app_settings.get("live_voice", {}).get("global_hotkeys_enabled", False))
        )
        self.live_hotkeys_enabled_checkbox.toggled.connect(self.on_live_global_hotkeys_toggled)
        hotkey_row.addWidget(self.live_hotkeys_enabled_checkbox)
        hotkey_row.addWidget(QLabel("Global Stop All"))
        self.live_stop_hotkey_label = QLabel(
            self.app_settings.get("live_voice", {}).get("stop_hotkey", "") or "Not set"
        )
        self.live_stop_hotkey_label.setObjectName("Muted")
        hotkey_row.addWidget(self.live_stop_hotkey_label)
        set_stop_hotkey = QPushButton("Set…")
        set_stop_hotkey.clicked.connect(self.set_live_stop_hotkey)
        hotkey_row.addWidget(set_stop_hotkey)
        clear_stop_hotkey = self._link(QPushButton("Clear"))
        clear_stop_hotkey.clicked.connect(self.clear_live_stop_hotkey)
        hotkey_row.addWidget(clear_stop_hotkey)
        hotkey_row.addStretch(1)
        self.live_hotkey_status_label = QLabel("")
        self.live_hotkey_status_label.setObjectName("Muted")
        hotkey_row.addWidget(self.live_hotkey_status_label)
        board_layout.addLayout(hotkey_row)
        layout.addWidget(board_card, 1)

        self.live_voice_shortcuts = []
        self._add_live_shortcut(page, "Ctrl+Return", self.live_submit)
        self._add_live_shortcut(page, "Ctrl+Enter", self.live_submit)
        self._add_live_shortcut(page, "Ctrl+.", self.live_stop_all)
        self._add_live_shortcut(page, "Ctrl+Shift+Return", self.live_repeat_last)
        self._add_live_shortcut(page, "Ctrl+Shift+Enter", self.live_repeat_last)
        self._add_live_shortcut(page, "Delete", self.live_remove_selected_queue_item)
        self._add_live_shortcut(page, "Alt+Up", lambda: self.live_move_selected_queue_item(-1))
        self._add_live_shortcut(page, "Alt+Down", lambda: self.live_move_selected_queue_item(1))
        self._add_live_shortcut(page, "Ctrl+Shift+Up", lambda: self.move_selected_soundboard_pad(-1))
        self._add_live_shortcut(page, "Ctrl+Shift+Down", lambda: self.move_selected_soundboard_pad(1))
        for number in range(1, 10):
            self._add_live_shortcut(
                page,
                f"Alt+{number}",
                lambda index=number - 1: self.trigger_soundboard_pad_index(index),
            )
            self._add_live_shortcut(
                page,
                f"Ctrl+Alt+{number}",
                lambda index=number - 1: self.select_soundboard_board_index(index),
            )

        self.pages.addWidget(page)
        try:
            self.media_devices.audioOutputsChanged.connect(self.refresh_live_audio_devices)
            self.media_devices.audioInputsChanged.connect(self.refresh_live_audio_devices)
        except Exception:
            pass
        self.refresh_live_audio_devices()
        self.refresh_live_voice_summary()
        self.refresh_live_voice_favorites()
        self.refresh_soundboard_boards()
        self.refresh_soundboard()
        self.refresh_soundboard_board_defaults()
        self.refresh_live_global_hotkeys()
        self.update_live_route_state()
        self.on_live_source_mode_changed(self.live_source_mode_combo.currentIndex())

    def _add_live_shortcut(self, page, sequence, callback):
        shortcut = QShortcut(QKeySequence(sequence), page)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self.live_voice_shortcuts.append(shortcut)
        return shortcut

    def refresh_live_voice_summary(self):
        if not hasattr(self, "live_voice_name_label"):
            return
        voice = None
        if self.active_voice_id:
            voice = self.voice_library.get(self.active_voice_id)
        self.live_voice_name_label.setText(
            voice.name if voice is not None else self.voice_chip.text()
        )
        loaded = self.loaded_entry() if self.model is not None else None
        self.live_model_label.setText(
            loaded["label"] if loaded else "No model loaded"
        )
        if getattr(self, "model_is_loading", False):
            readiness = "Voice model: getting ready…"
        elif self.model is not None:
            readiness = "Voice model: ready"
        else:
            readiness = "Voice model: load one to speak"
        self.live_model_label.setToolTip(readiness)
        mic_mode = (
            hasattr(self, "live_source_mode_combo")
            and self.live_source_mode_combo.currentData() == "mic_effects"
        )
        if mic_mode:
            mode = "Mic Effects"
        elif self.model is None:
            mode = "Unavailable"
        elif getattr(self.model, "native_streaming", False):
            mode = "Native streaming"
        elif getattr(self.model, "segmented_streaming", False):
            mode = "Segmented streaming"
        else:
            mode = "Buffered fallback"
        self.live_mode_label.setText(mode)
        if hasattr(self, "live_voice_origin_label"):
            origin = (voice.origin or voice.kind) if voice is not None else "default / current"
            self.live_voice_origin_label.setText(f"Origin: {origin}")
        if hasattr(self, "live_favorite_voice_combo"):
            self.refresh_live_voice_favorites()

    def refresh_live_voice_favorites(self):
        if not hasattr(self, "live_favorite_voice_combo"):
            return
        settings = self.app_settings.setdefault("live_voice", {})
        saved = list(settings.get("favorite_voice_ids", []) or [])
        voices = {voice.id: voice for voice in self.voice_library.voices}
        valid = [voice_id for voice_id in saved if voice_id in voices]
        if valid != saved:
            settings["favorite_voice_ids"] = valid
            self.save_app_settings()
        current = self.live_favorite_voice_combo.currentData()
        self.live_favorite_voice_combo.blockSignals(True)
        self.live_favorite_voice_combo.clear()
        if not valid:
            self.live_favorite_voice_combo.addItem("No favorite voices yet", None)
        else:
            for voice_id in valid:
                voice = voices[voice_id]
                self.live_favorite_voice_combo.addItem(voice.name, voice.id)
        preferred = self.active_voice_id if self.active_voice_id in valid else current
        index = self.live_favorite_voice_combo.findData(preferred)
        if index >= 0:
            self.live_favorite_voice_combo.setCurrentIndex(index)
        self.live_favorite_voice_combo.blockSignals(False)

    def favorite_current_live_voice(self):
        voice = self.voice_library.get(self.active_voice_id) if self.active_voice_id else None
        if voice is None:
            QMessageBox.information(
                self, "Quick voices", "Choose one of your saved voices before favoriting it."
            )
            return
        settings = self.app_settings.setdefault("live_voice", {})
        favorites = list(settings.get("favorite_voice_ids", []) or [])
        if voice.id not in favorites:
            favorites.append(voice.id)
            settings["favorite_voice_ids"] = favorites
            self.save_app_settings()
        self.refresh_live_voice_favorites()
        index = self.live_favorite_voice_combo.findData(voice.id)
        if index >= 0:
            self.live_favorite_voice_combo.setCurrentIndex(index)
        self.live_status_label.setText(f"Quick voice saved: {voice.name}")

    def remove_selected_favorite_voice(self):
        voice_id = self.live_favorite_voice_combo.currentData()
        if not voice_id:
            return
        settings = self.app_settings.setdefault("live_voice", {})
        settings["favorite_voice_ids"] = [
            item for item in list(settings.get("favorite_voice_ids", []) or [])
            if item != voice_id
        ]
        self.save_app_settings()
        self.refresh_live_voice_favorites()

    def use_selected_favorite_voice(self):
        voice_id = self.live_favorite_voice_combo.currentData()
        voice = self.voice_library.get(voice_id) if voice_id else None
        if voice is not None:
            self.use_voice(voice)

    @staticmethod
    def _device_id_hex(device):
        return LiveAudioOutput.device_key(device).hex()

    def _live_mic_settings(self):
        return self.app_settings.setdefault("live_voice", {}).setdefault(
            "microphone", {}
        )

    def current_live_microphone_device(self):
        if not hasattr(self, "live_mic_input_combo"):
            return None
        index = self.live_mic_input_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self.live_audio_inputs)):
            return None
        return self.live_audio_inputs[index]

    def refresh_live_microphone_devices(self):
        if not hasattr(self, "live_mic_input_combo"):
            return
        settings = self._live_mic_settings()
        saved_id = str(settings.get("device_id", "") or "")
        saved_name = str(settings.get("device_name", "") or "")
        combo = self.live_mic_input_combo
        combo.blockSignals(True)
        combo.clear()

        chosen = -1
        for index, device in enumerate(self.live_audio_inputs):
            if saved_id and self._device_id_hex(device) == saved_id:
                chosen = index
                break

        if saved_id and chosen < 0:
            combo.addItem(
                f"Unavailable: {saved_name or 'saved microphone'} — choose another input",
                None,
            )
        elif not saved_id:
            combo.addItem("Choose a microphone/input device…", None)

        for index, device in enumerate(self.live_audio_inputs):
            combo.addItem(device.description(), index)

        if chosen >= 0:
            item_index = combo.findData(chosen)
            if item_index >= 0:
                combo.setCurrentIndex(item_index)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def on_live_mic_input_changed(self, _index):
        device = self.current_live_microphone_device()
        settings = self._live_mic_settings()
        if device is None:
            settings["device_id"] = ""
            settings["device_name"] = ""
        else:
            settings["device_id"] = self._device_id_hex(device)
            settings["device_name"] = device.description()
        self.save_app_settings()

    def on_live_source_mode_changed(self, _index):
        mode = (
            self.live_source_mode_combo.currentData()
            if hasattr(self, "live_source_mode_combo")
            else "tts"
        )
        mic_mode = mode == "mic_effects"
        if not mic_mode and hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            self.stop_live_microphone()

        busy = bool(getattr(self, "live_voice_busy", False))
        if hasattr(self, "live_text_input"):
            self.live_text_input.setEnabled(not mic_mode and not busy)
        if hasattr(self, "live_speak_button"):
            self.live_speak_button.setEnabled(not mic_mode and not busy)
        for widget_name in (
            "live_mic_input_combo",
            "live_mic_effects_enabled",
            "live_mic_gain",
            "live_mic_tone",
            "live_mic_compressor",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(mic_mode and not busy)
        if hasattr(self, "live_mic_start_button"):
            self.live_mic_start_button.setEnabled(mic_mode)
        if mic_mode:
            self.live_mode_label.setText("Microphone effects")
            self.live_provenance_label.setText("Provenance: live microphone · not recorded")

    def on_live_mic_effect_setting_changed(self, *_args):
        if not hasattr(self, "live_mic_effects_enabled"):
            return
        settings = self._live_mic_settings()
        settings["effects_enabled"] = bool(self.live_mic_effects_enabled.isChecked())
        settings["gain_db"] = float(self.live_mic_gain.value())
        settings["tone"] = float(self.live_mic_tone.value())
        settings["compressor_enabled"] = bool(self.live_mic_compressor.isChecked())
        self.save_app_settings()

    def _live_mic_effects_config(self):
        if not self.live_mic_effects_enabled.isChecked():
            return MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        return MicrophoneEffectsConfig(
            gain_db=float(self.live_mic_gain.value()),
            tone=float(self.live_mic_tone.value()),
            compressor_enabled=bool(self.live_mic_compressor.isChecked()),
            compressor_threshold_db=-18.0,
            compressor_ratio=3.0,
            limiter_ceiling_db=-1.0,
        )

    def toggle_live_microphone(self):
        if self.live_mic_input.is_active():
            self.stop_live_microphone()
        else:
            self.start_live_microphone()

    def start_live_microphone(self):
        if self.live_source_mode_combo.currentData() != "mic_effects":
            return
        route_problem = self.live_route_problem()
        if route_problem:
            QMessageBox.information(self, "Mic Effects route", route_problem)
            return
        if (
            self.live_speech_thread is not None
            or self.live_voice_queue
            or self.live_audio_output.is_playing()
            or self.live_monitor_output.is_playing()
        ):
            QMessageBox.information(
                self,
                "Mic Effects",
                "Stop current Live Voice playback and clear queued speech before starting the microphone.",
            )
            return

        microphone = self.current_live_microphone_device()
        if microphone is None:
            QMessageBox.information(
                self, "Mic Effects", "Choose a microphone/input device first."
            )
            return

        output = self.current_live_audio_device()
        profile = self.active_live_route_profile()
        if (
            not profile.external
            and output is not None
            and not live_routes.probably_headphones(output.description())
        ):
            answer = QMessageBox.question(
                self,
                "Microphone feedback risk",
                "The selected local output does not look like headphones. "
                "Sending a live microphone to speakers can create loud feedback. Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.live_voice_last_error = ""
        try:
            meta = self.live_mic_input.configure(
                microphone,
                self._live_mic_effects_config(),
            )
            meta["mode"] = "microphone"
            self.on_live_stream_started(meta)
            if self.live_voice_last_error:
                return
            self.live_mic_input.start()
        except Exception as exc:
            self.live_mic_input.stop()
            self.live_audio_output.stop()
            self.live_monitor_output.stop()
            QMessageBox.warning(self, "Mic Effects", str(exc))
            return

        self.live_voice_stop_all_requested = False
        self._set_live_generation_busy(True)
        self.live_mic_start_button.setText("Stop microphone")
        self.live_mic_start_button.setEnabled(True)
        self.live_mic_status_label.setText(
            f"LIVE · {microphone.description()} · audio is not being recorded"
        )
        self.live_current_label.setText("Microphone effects active.")

    def stop_live_microphone(self):
        if not hasattr(self, "live_mic_input"):
            return
        was_active = self.live_mic_input.is_active()
        self.live_mic_input.stop()
        self.live_audio_output.stop()
        self.live_monitor_output.stop()
        self._set_live_generation_busy(False)
        if hasattr(self, "live_mic_start_button"):
            self.live_mic_start_button.setText("Start microphone")
        if hasattr(self, "live_mic_status_label"):
            self.live_mic_status_label.setText("Microphone stopped")
        if was_active:
            self.live_current_label.setText("Microphone stopped.")
            self.live_status_label.setText("Stopped")
            if self.live_voice_queue and self.live_speech_thread is None:
                self._live_start_next()

    def on_live_mic_frame(self, frame):
        if not self.live_mic_input.is_active():
            return
        self.on_live_frame(frame)

    def on_live_mic_error(self, message):
        self.live_voice_last_error = str(message)
        self.stop_live_microphone()
        self.live_status_label.setText(f"Microphone error: {message}")
        self.set_status_message(f"Status: Live Voice microphone error: {message}")

    def on_live_mic_stopped(self, metrics):
        self.live_last_generation_metrics = dict(metrics or {})
        self._refresh_live_stop_buttons()

    def active_live_route_profile(self):
        return self.live_route_store.active()

    def _device_index_for_id(self, device_id):
        if not device_id:
            return -1
        for index, device in enumerate(self.live_audio_devices):
            if self._device_id_hex(device) == device_id:
                return index
        return -1

    def _fill_live_device_combo(
        self, combo, saved_id, saved_name, *, allow_default=False, prompt="Choose an output device…"
    ):
        combo.blockSignals(True)
        combo.clear()
        chosen = self._device_index_for_id(saved_id)
        missing_saved = bool(saved_id) and chosen < 0

        if missing_saved:
            combo.addItem(f"Unavailable: {saved_name or 'saved output'} — choose another output", None)
        elif not saved_id and not allow_default:
            combo.addItem(prompt, None)

        for index, device in enumerate(self.live_audio_devices):
            label = device.description()
            if live_routes.probably_virtual_device(label):
                label += " · likely virtual"
            combo.addItem(label, index)

        if not saved_id and allow_default and self.live_audio_devices:
            default_key = self._device_id_hex(self.media_devices.defaultAudioOutput())
            chosen = self._device_index_for_id(default_key)
            if chosen < 0:
                chosen = 0

        if missing_saved or (not saved_id and not allow_default):
            combo.setCurrentIndex(0)
        elif chosen >= 0:
            data_index = combo.findData(chosen)
            if data_index >= 0:
                combo.setCurrentIndex(data_index)
        combo.blockSignals(False)
        return missing_saved

    def refresh_live_audio_devices(self):
        if not hasattr(self, "live_output_combo"):
            return
        self.live_audio_devices = list(self.media_devices.audioOutputs())
        self.live_audio_inputs = list(self.media_devices.audioInputs())
        self.refresh_live_microphone_devices()
        profile = self.active_live_route_profile()

        missing_primary = self._fill_live_device_combo(
            self.live_output_combo,
            profile.output_device_id,
            profile.output_device_name,
            allow_default=not profile.external,
            prompt="Choose the virtual/external output device…",
        )
        self._fill_live_device_combo(
            self.live_monitor_combo,
            profile.monitor_device_id,
            profile.monitor_device_name,
            allow_default=False,
            prompt="Choose a headphone/monitor output…",
        )
        self.live_monitor_checkbox.blockSignals(True)
        self.live_monitor_checkbox.setChecked(bool(profile.monitor_enabled))
        self.live_monitor_checkbox.blockSignals(False)
        self.live_monitor_combo.setEnabled(bool(profile.monitor_enabled) and not self.live_voice_busy)

        if missing_primary:
            self.live_external_armed = False
            self.live_status_label.setText(
                "Saved primary output is unavailable. Choose a new destination."
            )
        self.update_live_route_state()

    def current_live_audio_device(self):
        index = self.live_output_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self.live_audio_devices)):
            return None
        return self.live_audio_devices[index]

    def current_live_monitor_device(self):
        if not self.live_monitor_checkbox.isChecked():
            return None
        index = self.live_monitor_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self.live_audio_devices)):
            return None
        return self.live_audio_devices[index]

    def on_live_route_profile_changed(self, _index):
        profile_id = self.live_route_profile_combo.currentData()
        if not profile_id:
            return
        self.live_route_store.select(str(profile_id))
        self.live_external_armed = False
        self.live_monitor_output.stop()
        self.live_audio_output.stop()
        self.live_route_store.persist()
        self.save_app_settings()
        self.refresh_live_audio_devices()

    def on_live_output_changed(self, _index):
        device = self.current_live_audio_device()
        profile = self.active_live_route_profile()
        if device is None:
            profile.output_device_id = ""
            profile.output_device_name = ""
        else:
            profile.output_device_id = self._device_id_hex(device)
            profile.output_device_name = device.description()
        if profile.external:
            self.live_external_armed = False
        self.live_route_store.persist()
        self.save_app_settings()
        self.update_live_route_state()

    def on_live_monitor_toggled(self, enabled):
        profile = self.active_live_route_profile()
        profile.monitor_enabled = bool(enabled)
        if not enabled:
            self.live_monitor_output.stop()
        self.live_route_store.persist()
        self.save_app_settings()
        self.live_monitor_combo.setEnabled(bool(enabled) and not self.live_voice_busy)
        self.update_live_route_state()

    def on_live_monitor_changed(self, _index):
        device = self.current_live_monitor_device()
        profile = self.active_live_route_profile()
        if device is None:
            profile.monitor_device_id = ""
            profile.monitor_device_name = ""
        else:
            profile.monitor_device_id = self._device_id_hex(device)
            profile.monitor_device_name = device.description()
        self.live_route_store.persist()
        self.save_app_settings()

    def toggle_live_external_arm(self):
        profile = self.active_live_route_profile()
        if not profile.external:
            return
        if self.live_external_armed:
            self.live_external_armed = False
        else:
            device = self.current_live_audio_device()
            if device is None:
                QMessageBox.information(
                    self,
                    "External route",
                    "Choose the virtual/external output device before arming this route.",
                )
                return
            self.live_external_armed = True
        self.update_live_route_state()

    def paired_live_input_name(self):
        profile = self.active_live_route_profile()
        if not profile.external:
            return ""
        output = self.current_live_audio_device()
        if output is None:
            return ""
        input_names = [device.description() for device in self.live_audio_inputs]
        return live_routes.paired_input_hint(output.description(), input_names)

    def refresh_live_app_setup_hint(self):
        if not hasattr(self, "live_paired_input_label"):
            return
        profile = self.active_live_route_profile()
        if not profile.external:
            self.live_paired_input_label.setText("Not applicable for local output")
            self.live_paired_input_label.setToolTip("")
            self.live_copy_mic_button.setVisible(False)
            return

        paired = self.paired_live_input_name()
        self.live_copy_mic_button.setVisible(bool(paired))
        if paired:
            self.live_paired_input_label.setText(paired)
            self.live_paired_input_label.setToolTip(
                "Best-effort match from the selected virtual playback endpoint. "
                "Choose this recording endpoint as the microphone/input in the target app."
            )
        elif self.current_live_audio_device() is None:
            self.live_paired_input_label.setText("Choose an external output first")
            self.live_paired_input_label.setToolTip("")
        else:
            self.live_paired_input_label.setText(
                "No paired recording endpoint could be identified automatically"
            )
            self.live_paired_input_label.setToolTip(
                "Open the target app's microphone settings and choose the recording side "
                "of the virtual cable manually."
            )

    def copy_live_paired_input_name(self):
        paired = self.paired_live_input_name()
        if not paired:
            return
        QApplication.clipboard().setText(paired)
        self.live_status_label.setText("Microphone/input device name copied.")

    def show_live_app_setup(self):
        profile = self.active_live_route_profile()
        if not profile.external:
            QMessageBox.information(
                self,
                "Local output",
                "Local output needs no third-party microphone setup. Choose your speakers or headphones and use Test route.",
            )
            return

        setup = live_routes.APP_SETUP.get(profile.app) or live_routes.APP_SETUP["generic"]
        paired = self.paired_live_input_name()
        output = self.current_live_audio_device()
        output_name = output.description() if output is not None else "not selected"
        mic_name = paired or "the recording side of your selected virtual audio cable"

        numbered = "\n\n".join(
            f"{index}. {step}" for index, step in enumerate(setup["steps"], 1)
        )
        QMessageBox.information(
            self,
            setup["title"],
            f"This Voice Thing output:\n{output_name}\n\n"
            f"Target app microphone/input:\n{mic_name}\n\n"
            f"{numbered}\n\n"
            "The microphone pairing is advisory because Windows audio devices do not expose "
            "a universal render-to-capture relationship. Device names can also vary by driver version.",
        )

    def update_live_route_state(self):
        if not hasattr(self, "live_route_state"):
            return
        profile = self.active_live_route_profile()
        device_ok = self.current_live_audio_device() is not None
        self.live_route_setup_button.setVisible(profile.external)
        self.refresh_live_app_setup_hint()
        if profile.external:
            self.live_arm_button.setVisible(True)
            self.live_arm_button.setText(
                "Disarm external route" if self.live_external_armed else "Arm external route"
            )
            if self.live_external_armed and device_ok:
                self.live_route_state.setText("ARMED")
                description = self.current_live_audio_device().description()
                if not live_routes.probably_virtual_device(description):
                    self.live_route_state.setToolTip(
                        "This output is armed as external, but its name is not recognized as a common virtual audio device."
                    )
                else:
                    self.live_route_state.setToolTip("External audio output is armed for this app session.")
            else:
                self.live_route_state.setText("DISARMED")
                self.live_route_state.setToolTip(
                    "External routes start disarmed each time This Voice Thing launches."
                )
        else:
            self.live_external_armed = False
            self.live_arm_button.setVisible(False)
            self.live_route_state.setText("LOCAL ONLY")
            self.live_route_state.setToolTip("Speech is routed only to the selected local output.")

        can_speak = (
            device_ok
            and not getattr(self, "model_is_loading", False)
            and not self.is_generating
            and not self.api_busy
            and (not profile.external or self.live_external_armed)
        )
        self.live_speak_button.setEnabled(can_speak)
        self.live_monitor_combo.setEnabled(
            self.live_monitor_checkbox.isChecked() and not self.live_voice_busy
        )

    @staticmethod
    def _estimate_live_item_seconds(text):
        text = str(text or "").strip()
        return max(0.5, len(text) / APPROX_SPEECH_CHARS_PER_SECOND) if text else 0.0

    def _live_outstanding_items(self):
        count = len(self.live_voice_queue)
        seconds = sum(
            float(item.get("estimated_seconds") or self._estimate_live_item_seconds(item.get("text", "")))
            for item in self.live_voice_queue
        )
        if self.live_voice_current is not None:
            count += 1
            seconds += float(
                self.live_voice_current.get("estimated_seconds")
                or self._estimate_live_item_seconds(self.live_voice_current.get("text", ""))
            )
        return count, seconds

    def _enqueue_live_item(self, item):
        text = str((item or {}).get("text") or "").strip()
        if not text:
            return False
        count, seconds = self._live_outstanding_items()
        item_seconds = float(
            item.get("estimated_seconds")
            or self._estimate_live_item_seconds(text)
        )
        if count >= MAX_LIVE_QUEUE_ITEMS:
            self.live_status_label.setText(
                f"Queue limit reached ({MAX_LIVE_QUEUE_ITEMS} outstanding items)."
            )
            return False
        if seconds + item_seconds > MAX_LIVE_QUEUE_SECONDS:
            self.live_status_label.setText(
                "Queue limit reached (about 10 minutes of outstanding speech)."
            )
            return False
        self.live_voice_queue.append(item)
        self._refresh_live_queue()
        self.live_stop_all_requested = False
        return True

    def live_route_problem(self):
        profile = self.active_live_route_profile()
        if self.current_live_audio_device() is None:
            return "Choose a valid primary audio output first."
        if profile.external and not self.live_external_armed:
            return (
                "The external route is disarmed. Arm it explicitly before sending speech "
                "to a virtual microphone or other external destination."
            )
        return ""

    def live_test_route(self):
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            QMessageBox.information(
                self, "Test route", "Stop the live microphone before running the spoken route test."
            )
            return
        problem = self.live_route_problem()
        if problem:
            QMessageBox.information(self, "Test route", problem)
            return
        if self.model is None:
            QMessageBox.information(
                self, "Test route", "Load a voice model first so the route test can speak."
            )
            return
        if getattr(self, "model_is_loading", False) or self.is_generating or self.api_busy:
            QMessageBox.information(
                self, "Test route", "The model is busy. Try the route test when generation is idle."
            )
            return
        if self._enqueue_live_item({"text": "This Voice Thing route test."}):
            if self.live_speech_thread is None:
                self._live_start_next()

    def _live_generate_kwargs(self):
        return {
            "exaggeration": self.exaggeration_slider.get_value(),
            "temperature": self.temp_slider.get_value(),
            "cfg_weight": self.cfg_slider.get_value(),
            "repetition_penalty": self.repetition_penalty,
            "min_p": self.min_p,
            "top_p": self.top_p,
        }

    def _make_live_session(self, text):
        if self.model is None:
            raise RuntimeError("Load a model before using Live Voice.")
        if isinstance(self.model, vibevoice_engine.VibeVoiceModel):
            raise RuntimeError(
                "VibeVoice conversation rendering is still Generate-only in this first Live Voice slice."
            )
        problem = self.prepare_qwen_generation()
        if problem:
            raise RuntimeError(problem)
        finishing = self.current_finishing_settings()
        return LiveSpeechSession(
            self.model,
            text,
            audio_prompt_path=self.reference_path or None,
            language_id=self.language_combo.currentData() or "en",
            paragraph_pause=finishing.paragraph_pause,
            pronunciations=self.pronunciations,
            generate_kwargs=self._live_generate_kwargs(),
        )

    def live_submit(self):
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            QMessageBox.information(
                self, "Live Voice", "Stop Mic Effects before sending typed speech."
            )
            return
        text = self.live_text_input.toPlainText().strip()
        if not text:
            return
        if self.model is None:
            QMessageBox.information(self, "Live Voice", "Load a model first.")
            return
        if getattr(self, "model_is_loading", False) or self.is_generating or self.api_busy:
            QMessageBox.information(
                self,
                "Live Voice",
                "The model is busy with another generation or load. Try again when it is ready.",
            )
            return
        route_problem = self.live_route_problem()
        if route_problem:
            QMessageBox.information(self, "Live Voice route", route_problem)
            return

        if not self._enqueue_live_item({"text": text}):
            return
        self.live_text_input.clear()
        if self.live_speech_thread is None:
            self._live_start_next()

    def _live_start_next(self):
        if self.live_speech_thread is not None:
            return
        if not self.live_voice_queue:
            self.live_voice_current = None
            self.live_current_label.setText("Nothing speaking.")
            self.live_audio_output.finish_input()
            if self.live_monitor_output.route_description():
                self.live_monitor_output.finish_input()
            self._set_live_generation_busy(False)
            return

        item = self.live_voice_queue.pop(0)
        self.live_voice_current = item
        self.live_voice_last_error = ""
        self._refresh_live_queue()
        try:
            if item.get("audio_path"):
                session = AudioFileSpeechSession(item["audio_path"], item.get("label") or "Soundboard audio")
            elif item.get("cached_path"):
                session = CachedSpeechSession(
                    item["cached_path"],
                    item.get("label") or "Soundboard",
                    provenance=item.get("cache_provenance") or "soundboard-cache-unknown",
                )
            else:
                session = self._make_live_session(item["text"])
                if item.get("cache_key"):
                    item["_cache_pcm"] = bytearray()
                    item["_cache_sample_rate"] = 0
        except Exception as exc:
            self.live_voice_current = None
            self.live_status_label.setText(f"Cannot speak: {exc}")
            self._set_live_generation_busy(False)
            return

        self._set_live_generation_busy(True)
        self.live_current_label.setText(f"Speaking: {item['text']}")
        self.live_status_label.setText(f"Starting · {session.delivery_mode}")
        thread = LiveSpeechThread(session, self)
        thread.stream_started.connect(self.on_live_stream_started)
        thread.frame_ready.connect(self.on_live_frame)
        thread.session_complete.connect(self.on_live_session_complete)
        thread.error_occurred.connect(self.on_live_speech_error)
        thread.finished.connect(self.on_live_thread_finished)
        self.live_speech_thread = thread
        thread.start()

    def on_live_stream_started(self, meta):
        device = self.current_live_audio_device()
        if device is None:
            self.on_live_audio_error("The selected primary audio output disappeared.")
            return
        mode = str(meta.get("mode") or "buffered")
        low_latency = mode == "microphone"
        sink_kwargs = (
            {"start_buffer_seconds": 0.02, "sink_buffer_seconds": 0.10}
            if low_latency
            else {}
        )
        try:
            self.live_audio_output.configure(
                device,
                int(meta["sample_rate"]),
                **sink_kwargs,
            )
        except Exception as exc:
            self.on_live_audio_error(str(exc))
            return

        monitor = self.current_live_monitor_device()
        self.live_monitor_status.setText("")
        if self.live_monitor_checkbox.isChecked():
            if monitor is None:
                self.live_monitor_output.stop()
                self.live_monitor_status.setText("Monitor unavailable; primary route continues.")
            elif self._device_id_hex(monitor) == self._device_id_hex(device):
                self.live_monitor_output.stop()
                self.live_monitor_status.setText("Monitor matches primary; not duplicated.")
            else:
                try:
                    self.live_monitor_output.configure(
                        monitor,
                        int(meta["sample_rate"]),
                        **sink_kwargs,
                    )
                    if (
                        self.active_live_route_profile().external
                        and not live_routes.probably_headphones(monitor.description())
                    ):
                        self.live_monitor_status.setText(
                            f"Monitoring: {monitor.description()} · feedback risk if a physical mic can hear it"
                        )
                    else:
                        self.live_monitor_status.setText(f"Monitoring: {monitor.description()}")
                except Exception as exc:
                    self.on_live_monitor_error(str(exc))

        self.live_mode_label.setText(
            {
                "native": "Native streaming",
                "segmented": "Segmented streaming",
                "cached": "Cached playback",
                "audio": "Audio clip",
                "buffered": "Buffered fallback",
                "microphone": "Mic Effects",
            }.get(mode, mode.replace("_", " ").title())
        )
        provenance = str(meta.get("provenance") or "")
        provenance_labels = {
            "live-native-unwatermarked": "Provenance: native live · Perth watermark not applied",
            "live-segmented-engine-watermark": "Provenance: segmented live · engine watermark applied",
            "buffered-engine-default": "Provenance: buffered · engine/default policy",
            "soundboard-cache-unknown": "Provenance: cached · original watermark state unknown",
            "soundboard-audio-file": "Provenance: local audio clip · source file",
            "microphone-passthrough": "Provenance: live microphone · pass-through · not recorded",
            "microphone-dsp": "Provenance: live microphone · local DSP · not recorded",
        }
        if mode == "cached":
            cached_labels = {
                "live-native-unwatermarked":
                    "Provenance: cached from native live · Perth watermark not applied",
                "live-segmented-engine-watermark":
                    "Provenance: cached from segmented live · engine watermark applied",
                "buffered-engine-default":
                    "Provenance: cached from buffered render · engine/default policy",
                "soundboard-cache-unknown":
                    "Provenance: cached · original watermark state unknown",
            }
            self.live_provenance_label.setText(
                cached_labels.get(
                    provenance,
                    f"Provenance: cached source · {provenance or 'unknown'}",
                )
            )
        elif provenance in provenance_labels:
            self.live_provenance_label.setText(provenance_labels[provenance])
        elif provenance:
            self.live_provenance_label.setText(f"Provenance: {provenance}")
        else:
            self.live_provenance_label.setText("Provenance: unavailable")
        verb = (
            "Listening"
            if mode == "microphone"
            else ("Playing" if mode in ("cached", "audio") else "Generating")
        )
        self.live_status_label.setText(
            f"{verb} · {mode} · {device.description()}"
        )

    def on_live_frame(self, frame):
        if self.live_voice_current and self.live_voice_current.get("cache_key"):
            self.live_voice_current["_cache_pcm"].extend(frame.pcm)
            self.live_voice_current["_cache_sample_rate"] = int(frame.sample_rate)
        try:
            self.live_audio_output.push(frame.pcm)
        except Exception as exc:
            self.on_live_audio_error(str(exc))
            return
        if self.live_monitor_output.route_description():
            try:
                self.live_monitor_output.push(frame.pcm)
            except Exception as exc:
                self.on_live_monitor_error(str(exc))

    def on_live_session_complete(self, metrics):
        self.live_last_generation_metrics = dict(metrics)
        if not metrics.get("cancelled") and self.live_voice_current:
            if not self.live_voice_current.get("audio_path"):
                self.live_voice_history.append({
                    "text": self.live_voice_current["text"],
                    "metrics": dict(metrics),
                })
                self.live_voice_history = self.live_voice_history[-50:]
            cache_key = self.live_voice_current.get("cache_key")
            pcm = self.live_voice_current.get("_cache_pcm")
            sample_rate = self.live_voice_current.get("_cache_sample_rate")
            pad_id = self.live_voice_current.get("soundboard_pad_id")
            if cache_key and pcm and sample_rate and pad_id:
                try:
                    self.soundboard_store.write_pcm_cache(cache_key, pcm, sample_rate)
                    pad = self.soundboard_store.get_pad(pad_id)
                    if pad is not None:
                        pad.cache_key = cache_key
                        pad.cache_provenance = str(metrics.get("provenance") or "")
                        self.soundboard_store.save()
                        self.soundboard_store.garbage_collect_cache()
                    self.refresh_soundboard()
                except Exception as exc:
                    self.soundboard_status_label.setText(f"Could not cache pad: {exc}")
        ttfa = metrics.get("ttfa_seconds")
        rtf = metrics.get("rtf")
        details = []
        if ttfa is not None:
            details.append(f"TTFA {ttfa:.2f}s")
        if rtf is not None:
            details.append(f"RTF {rtf:.2f}")
        if details:
            self.live_status_label.setText(" · ".join(details))

    def on_live_thread_finished(self):
        self.live_speech_thread = None
        self.live_voice_current = None
        if self.live_voice_stop_all_requested:
            self._set_live_generation_busy(False)
            self.live_current_label.setText("Stopped.")
            return
        if self.live_voice_last_error:
            self._set_live_generation_busy(False)
            return
        if self.live_voice_queue:
            self._live_start_next()
        else:
            self.live_audio_output.finish_input()
            if self.live_monitor_output.route_description():
                self.live_monitor_output.finish_input()
            self._set_live_generation_busy(False)
            self.live_current_label.setText("Finishing playback…")

    def live_stop_current(self):
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            self.stop_live_microphone()
            return
        if (
            self.live_speech_thread is None
            and not self.live_audio_output.is_playing()
            and not self.live_monitor_output.is_playing()
        ):
            return
        self.live_audio_output.stop()
        self.live_monitor_output.stop()
        if self.live_speech_thread is not None:
            self.live_speech_thread.stop()
            self.live_status_label.setText("Stopping current…")
        else:
            self._set_live_generation_busy(False)
            self.live_current_label.setText("Stopped.")
            self.live_status_label.setText("Stopped")

    def live_stop_all(self):
        self.live_voice_stop_all_requested = True
        self.live_voice_queue.clear()
        self._refresh_live_queue()
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            self.stop_live_microphone()
        self.live_audio_output.stop()
        self.live_monitor_output.stop()
        if self.live_speech_thread is not None:
            self.live_speech_thread.stop()
            self.live_status_label.setText("Stopping…")
        else:
            self._set_live_generation_busy(False)
            self.live_status_label.setText("Stopped")
            self.live_current_label.setText("Nothing speaking.")

    def copy_live_diagnostics(self):
        profile = self.active_live_route_profile()
        voice = self.voice_library.get(self.active_voice_id) if self.active_voice_id else None
        loaded = self.loaded_entry() if self.model is not None else None
        primary = self.current_live_audio_device()
        monitor = self.current_live_monitor_device()
        _count, outstanding_seconds = self._live_outstanding_items()
        payload = {
            "model": (
                {
                    "label": loaded.get("label"),
                    "repo_id": loaded.get("repo_id"),
                    "backend": loaded.get("backend"),
                    "mode": loaded.get("mode"),
                }
                if loaded is not None
                else None
            ),
            "voice": (
                {
                    "name": voice.name,
                    "kind": voice.kind,
                    "origin": voice.origin or "",
                }
                if voice is not None
                else {"name": self.live_voice_name_label.text(), "kind": "", "origin": ""}
            ),
            "delivery": {
                "mode_label": self.live_mode_label.text(),
                "provenance_label": self.live_provenance_label.text(),
                "generation": dict(self.live_last_generation_metrics),
            },
            "route": {
                "profile_id": profile.id,
                "profile_name": profile.name,
                "external": bool(profile.external),
                "armed": bool(self.live_external_armed) if profile.external else False,
                "primary_device": primary.description() if primary is not None else None,
                "monitor_enabled": bool(self.live_monitor_checkbox.isChecked()),
                "monitor_device": monitor.description() if monitor is not None else None,
            },
            "primary_sink": self.live_audio_output.last_stats(),
            "monitor_sink": self.live_monitor_output.last_stats(),
            "microphone": (
                self.live_mic_input.last_stats()
                if hasattr(self, "live_mic_input")
                else {}
            ),
            "queue": {
                "pending_items": len(self.live_voice_queue),
                "estimated_outstanding_seconds": round(outstanding_seconds, 2),
            },
        }
        QApplication.clipboard().setText(json.dumps(payload, indent=2, ensure_ascii=False))
        self.live_status_label.setText("Live Voice diagnostics copied.")

    def live_remove_selected_queue_item(self):
        row = self.live_queue_list.currentRow()
        if not (0 <= row < len(self.live_voice_queue)):
            return
        removed = self.live_voice_queue.pop(row)
        self._refresh_live_queue()
        self.live_status_label.setText(
            f"Removed queued item: {str(removed.get('text') or '')[:80]}"
        )
        if self.live_queue_list.count():
            self.live_queue_list.setCurrentRow(min(row, self.live_queue_list.count() - 1))

    def live_move_selected_queue_item(self, direction):
        row = self.live_queue_list.currentRow()
        if not (0 <= row < len(self.live_voice_queue)):
            return
        target = row + int(direction)
        if not (0 <= target < len(self.live_voice_queue)):
            return
        item = self.live_voice_queue.pop(row)
        self.live_voice_queue.insert(target, item)
        self._refresh_live_queue()
        self.live_queue_list.setCurrentRow(target)
        self.live_status_label.setText("Queued speech reordered.")

    def live_clear_history(self):
        count = len(self.live_voice_history)
        self.live_voice_history.clear()
        self.live_status_label.setText(
            f"Cleared {count} session-history item(s)."
            if count
            else "Session history is already empty."
        )

    def live_clear_queue(self):
        self.live_voice_queue.clear()
        self._refresh_live_queue()

    def live_repeat_last(self):
        if not self.live_voice_history:
            return
        self.live_text_input.setPlainText(self.live_voice_history[-1]["text"])
        self.live_submit()

    def _soundboard_pronunciation_fingerprint(self):
        return [
            (rule.word, rule.say, rule.whole_word, rule.match_case, rule.enabled)
            for rule in self.pronunciations.rules
        ] if self.pronunciations.enabled else []

    def _soundboard_synthesis_context(self, pad):
        voice = self.voice_library.get(pad.voice_id) if pad.voice_id else None
        clip_path = self.voice_library.clip_path(voice) if voice is not None and voice.has_clip else ""
        transcript = voice_library.read_transcript(clip_path) if clip_path else ""
        return {
            "voice_fingerprint": soundboard.voice_fingerprint(voice, clip_path, transcript),
            "pronunciations": self._soundboard_pronunciation_fingerprint(),
            "synthesis_settings": {
                "temperature": pad.temperature,
                "exaggeration": pad.exaggeration,
                "cfg_weight": pad.cfg_weight,
                "repetition_penalty": pad.repetition_penalty,
                "min_p": pad.min_p,
                "top_p": pad.top_p,
            },
        }

    def _soundboard_cache_key(self, pad):
        return self.soundboard_store.cache_digest(
            pad, self._soundboard_synthesis_context(pad)
        )

    def _current_soundboard_pad(self, label, text):
        loaded = self.loaded_entry()
        worker = self.active_qwen_model()
        return soundboard.Pad(
            label=label,
            text=text,
            voice_id=self.active_voice_id or "",
            model_repo_id=loaded["repo_id"] if loaded else "",
            model_backend=loaded.get("backend", "") if loaded else "",
            model_mode=loaded.get("mode", "") if loaded else "",
            language=self.language_combo.currentData() or "en",
            style=self.qwen_instruct_input.text().strip() if worker is not None else "",
            temperature=float(self.temp_slider.get_value()),
            exaggeration=float(self.exaggeration_slider.get_value()),
            cfg_weight=float(self.cfg_slider.get_value()),
            repetition_penalty=float(self.repetition_penalty),
            min_p=float(self.min_p),
            top_p=float(self.top_p),
            interrupt_policy="",
        )

    def save_soundboard_pad(self):
        text = self.live_text_input.toPlainText().strip()
        if not text and self.live_voice_history:
            text = self.live_voice_history[-1]["text"]
        if not text:
            QMessageBox.information(self, "Soundboard", "Type a phrase first, or speak something you can save.")
            return
        default = " ".join(text.split())[:32] or "Phrase"
        label, accepted = QInputDialog.getText(self, "Save soundboard pad", "Pad label:", text=default)
        if not accepted or not label.strip():
            return
        pad = self.soundboard_store.add_pad(self._current_soundboard_pad(label.strip(), text))
        self.refresh_soundboard_boards()
        self.refresh_soundboard(select_id=pad.id)
        if self.model is None:
            self.soundboard_status_label.setText(
                "Pad saved. Load its voice/model later to build the local cache."
            )
            return
        self.soundboard_status_label.setText("Pad saved. Building its local cache…")
        self._queue_soundboard_pad(pad, force_generate=True)

    def add_soundboard_audio_pad(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Add soundboard audio clip",
            self.script_dir,
            "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif);;All files (*)",
        )
        if not path:
            return
        try:
            AudioFileSpeechSession(path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Soundboard audio",
                f"This audio file could not be opened by the installed audio decoder:\n\n{exc}",
            )
            return
        default = os.path.splitext(os.path.basename(path))[0] or "Audio clip"
        label, accepted = QInputDialog.getText(
            self, "Add soundboard audio clip", "Pad label:", text=default
        )
        if not accepted or not label.strip():
            return
        try:
            pad = self.soundboard_store.import_audio_pad(path, label.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Soundboard audio", f"Could not import the clip:\n\n{exc}")
            return
        self.refresh_soundboard_boards()
        self.refresh_soundboard(select_id=pad.id)
        self.refresh_live_global_hotkeys()
        self.soundboard_status_label.setText(
            f"Imported {pad.label}. The soundboard now owns a local copy."
        )

    def clear_soundboard_cache(self):
        answer = QMessageBox.question(
            self,
            "Clear soundboard cache",
            "Delete all cached soundboard TTS audio? Boards, pads, imported audio clips, "
            "favorites, tags and hotkeys will remain.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.soundboard_store.clear_cache()
        self.refresh_soundboard()
        self.soundboard_status_label.setText(
            f"Cleared {removed} cached TTS audio file(s)."
        )

    def refresh_soundboard_boards(self, select_id=None):
        if not hasattr(self, "soundboard_board_combo"):
            return
        selected = select_id or self.soundboard_store.active_board_id
        self.soundboard_board_combo.blockSignals(True)
        self.soundboard_board_combo.clear()
        for board in self.soundboard_store.boards:
            self.soundboard_board_combo.addItem(
                f"{board.name} ({len(board.pads)})", board.id
            )
        index = self.soundboard_board_combo.findData(selected)
        self.soundboard_board_combo.setCurrentIndex(max(0, index))
        self.soundboard_board_combo.blockSignals(False)

    def refresh_soundboard_board_defaults(self):
        if not hasattr(self, "soundboard_defaults_label"):
            return
        board = self.soundboard_store.active_board()
        if board is None:
            self.soundboard_defaults_label.setText("Voice: none · Route: none")
            return

        voice_name = "none"
        if board.default_voice_id:
            voice = self.voice_library.get(board.default_voice_id)
            if voice is not None:
                voice_name = voice.name
            else:
                board.default_voice_id = ""
                self.soundboard_store.save()
                voice_name = "missing voice cleared"

        route_name = "none"
        if board.route_profile:
            profile = self.live_route_store.get(board.route_profile)
            if profile is not None:
                route_name = profile.name
            else:
                board.route_profile = ""
                self.soundboard_store.save()
                route_name = "missing route cleared"

        self.soundboard_defaults_label.setText(
            f"Voice: {voice_name} · Route: {route_name}"
        )
        if hasattr(self, "soundboard_board_policy_combo"):
            self.soundboard_board_policy_combo.blockSignals(True)
            index = self.soundboard_board_policy_combo.findData(
                board.default_interrupt_policy or "queue"
            )
            self.soundboard_board_policy_combo.setCurrentIndex(max(0, index))
            self.soundboard_board_policy_combo.blockSignals(False)

    def on_soundboard_board_policy_changed(self, _index):
        board = self.soundboard_store.active_board()
        if board is None:
            return
        policy = str(self.soundboard_board_policy_combo.currentData() or "queue")
        board.default_interrupt_policy = policy
        self.soundboard_store.save()
        self.soundboard_status_label.setText(
            f"{board.name} default trigger behavior: {self.soundboard_board_policy_combo.currentText()}"
        )

    def set_soundboard_default_voice(self):
        board = self.soundboard_store.active_board()
        voice = self.voice_library.get(self.active_voice_id) if self.active_voice_id else None
        if board is None:
            return
        if voice is None:
            QMessageBox.information(
                self,
                "Board default voice",
                "Pick one of your saved voices first. Built-in transient selections are not stored as board defaults.",
            )
            return
        board.default_voice_id = voice.id
        self.soundboard_store.save()
        self.refresh_soundboard_board_defaults()
        self.soundboard_status_label.setText(
            f"{board.name} will prefer {voice.name} when the board is selected."
        )

    def clear_soundboard_default_voice(self):
        board = self.soundboard_store.active_board()
        if board is None:
            return
        board.default_voice_id = ""
        self.soundboard_store.save()
        self.refresh_soundboard_board_defaults()

    def set_soundboard_default_route(self):
        board = self.soundboard_store.active_board()
        profile = self.active_live_route_profile()
        if board is None or profile is None:
            return
        board.route_profile = profile.id
        self.soundboard_store.save()
        self.refresh_soundboard_board_defaults()
        self.soundboard_status_label.setText(
            f"{board.name} will prefer the {profile.name} route. External routes still require arming."
        )

    def clear_soundboard_default_route(self):
        board = self.soundboard_store.active_board()
        if board is None:
            return
        board.route_profile = ""
        self.soundboard_store.save()
        self.refresh_soundboard_board_defaults()

    def soundboard_defaults_can_apply(self):
        return not (
            self.live_voice_busy
            or self.live_voice_queue
            or self.live_audio_output.is_playing()
            or self.live_monitor_output.is_playing()
            or getattr(self, "model_is_loading", False)
            or self.is_generating
            or self.api_busy
        )

    def apply_soundboard_board_defaults(self, board):
        if board is None:
            return
        if not self.soundboard_defaults_can_apply():
            self.soundboard_status_label.setText(
                f"{board.name} selected. Its defaults were not applied while Live Voice was busy."
            )
            return

        if board.route_profile:
            index = self.live_route_profile_combo.findData(board.route_profile)
            if index >= 0:
                self.live_external_armed = False
                if self.live_route_profile_combo.currentIndex() != index:
                    self.live_route_profile_combo.setCurrentIndex(index)
                else:
                    self.live_route_store.select(board.route_profile)
                    self.refresh_live_audio_devices()
                    self.update_live_route_state()

        if board.default_voice_id:
            voice = self.voice_library.get(board.default_voice_id)
            if voice is None:
                board.default_voice_id = ""
                self.soundboard_store.save()
            elif self.active_voice_id != voice.id:
                self.use_voice(voice)

        self.refresh_soundboard_board_defaults()

    def on_soundboard_board_changed(self, _index):
        board_id = self.soundboard_board_combo.currentData()
        if not board_id:
            return
        board = self.soundboard_store.select_board(str(board_id))
        if board is None:
            return
        self.refresh_soundboard()
        self.refresh_live_global_hotkeys()
        can_apply = self.soundboard_defaults_can_apply()
        self.apply_soundboard_board_defaults(board)
        self.live_board_defaults_applied = can_apply
        if can_apply:
            self.soundboard_status_label.setText(
                f"{board.name}: {len(board.pads)} pad(s)"
            )

    def create_soundboard_board(self):
        name, accepted = QInputDialog.getText(
            self, "New soundboard", "Board name:", text="New board"
        )
        if not accepted or not name.strip():
            return
        board = self.soundboard_store.create_board(name.strip())
        self.refresh_soundboard_boards(select_id=board.id)
        self.refresh_soundboard()
        self.refresh_live_global_hotkeys()
        self.refresh_soundboard_board_defaults()
        self.soundboard_status_label.setText(f"Created {board.name}")

    def rename_soundboard_board(self):
        board = self.soundboard_store.active_board()
        if board is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename soundboard", "Board name:", text=board.name
        )
        if not accepted or not name.strip():
            return
        board = self.soundboard_store.rename_board(board.id, name.strip())
        self.refresh_soundboard_boards(select_id=board.id)
        self.refresh_soundboard_board_defaults()
        self.soundboard_status_label.setText(f"Renamed board to {board.name}")

    def delete_soundboard_board(self):
        board = self.soundboard_store.active_board()
        if board is None:
            return
        if len(self.soundboard_store.boards) <= 1:
            QMessageBox.information(
                self, "Delete soundboard", "The last soundboard cannot be deleted."
            )
            return
        if self.live_voice_busy or self.live_voice_queue or self.live_audio_output.is_playing() \
                or self.live_monitor_output.is_playing():
            QMessageBox.information(
                self,
                "Delete soundboard",
                "Stop Live Voice and clear the queue before deleting a board.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete soundboard",
            f"Delete “{board.name}” and its {len(board.pads)} pad(s)?\n\n"
            "Owned audio clips and unreferenced cached TTS files will also be deleted.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.soundboard_store.remove_board(board.id)
        self.refresh_soundboard_boards()
        self.refresh_soundboard()
        self.refresh_live_global_hotkeys()
        self.refresh_soundboard_board_defaults()
        if removed is not None:
            self.soundboard_status_label.setText(f"Deleted board {removed.name}")

    def select_soundboard_board_index(self, index):
        if not (0 <= index < len(self.soundboard_store.boards)):
            return
        board = self.soundboard_store.select_board(self.soundboard_store.boards[index].id)
        if board is None:
            return
        self.refresh_soundboard_boards()
        self.refresh_soundboard()
        self.refresh_soundboard_board_defaults()
        self.apply_soundboard_board_defaults(board)
        self.refresh_live_global_hotkeys()
        self.soundboard_status_label.setText(f"Board: {board.name}")

    def trigger_soundboard_pad_index(self, index):
        board = self.soundboard_store.active_board()
        if board is None or not (0 <= index < len(board.pads)):
            return
        self._queue_soundboard_pad(board.pads[index])

    def move_selected_soundboard_pad(self, direction):
        pad = self.selected_soundboard_pad()
        if pad is None:
            return
        if self.soundboard_store.move_pad(pad.id, direction):
            self.refresh_soundboard(select_id=pad.id)
            self.refresh_live_global_hotkeys()
            self.soundboard_status_label.setText(f"Moved {pad.label}.")

    def toggle_selected_soundboard_favorite(self):
        pad = self.selected_soundboard_pad()
        if pad is None:
            return
        pad.favorite = not bool(pad.favorite)
        self.soundboard_store.save()
        self.refresh_soundboard(select_id=pad.id)
        self.soundboard_status_label.setText(
            f"{'Favorited' if pad.favorite else 'Unfavorited'} {pad.label}"
        )

    def edit_selected_soundboard_tags(self):
        pad = self.selected_soundboard_pad()
        if pad is None:
            return
        current = ", ".join(pad.tags or [])
        value, accepted = QInputDialog.getText(
            self,
            "Soundboard tags",
            "Comma-separated tags:",
            text=current,
        )
        if not accepted:
            return
        seen = set()
        tags = []
        for item in value.split(","):
            tag = item.strip()
            key = tag.casefold()
            if tag and key not in seen:
                seen.add(key)
                tags.append(tag)
        pad.tags = tags
        self.soundboard_store.save()
        self.refresh_soundboard(select_id=pad.id)

    def set_selected_soundboard_behavior(self):
        pad = self.selected_soundboard_pad()
        if pad is None:
            return
        options = [
            ("Use board default", ""),
            ("Queue", "queue"),
            ("Interrupt current", "interrupt"),
            ("Ignore if busy", "ignore"),
        ]
        current_value = pad.interrupt_policy or ""
        current_index = next(
            (
                index
                for index, (_label, value) in enumerate(options)
                if value == current_value
            ),
            0,
        )
        label, accepted = QInputDialog.getItem(
            self,
            "Pad trigger behavior",
            "When this pad is triggered:",
            [item[0] for item in options],
            current_index,
            False,
        )
        if not accepted:
            return
        pad.interrupt_policy = next(
            value for option_label, value in options if option_label == label
        )
        self.soundboard_store.save()
        self.refresh_soundboard(select_id=pad.id)

    def _soundboard_trigger_policy(self, pad):
        if pad.interrupt_policy in ("queue", "interrupt", "ignore"):
            return pad.interrupt_policy
        board = self.soundboard_store.active_board()
        if board is not None and board.default_interrupt_policy in (
            "queue",
            "interrupt",
            "ignore",
        ):
            return board.default_interrupt_policy
        return "queue"

    def _soundboard_is_busy(self):
        return bool(
            self.live_speech_thread is not None
            or self.live_voice_queue
            or self.live_audio_output.is_playing()
            or self.live_monitor_output.is_playing()
            or (
                hasattr(self, "live_mic_input")
                and self.live_mic_input.is_active()
            )
        )

    def _enqueue_soundboard_item(self, item, pad, interactive=True):
        policy = self._soundboard_trigger_policy(pad)
        mic_active = (
            hasattr(self, "live_mic_input") and self.live_mic_input.is_active()
        )
        busy = self._soundboard_is_busy()
        if policy == "ignore" and busy:
            self.soundboard_status_label.setText(
                f"{pad.label}: ignored because Live Voice is busy."
            )
            return False

        if not self._enqueue_live_item(item):
            self.soundboard_status_label.setText(
                f"{pad.label}: queue limit reached."
            )
            return False

        if policy == "interrupt" and busy:
            # _enqueue_live_item appended the new request. Move it to the front so it
            # becomes the next utterance after the current output is cancelled.
            queued = self.live_voice_queue.pop()
            self.live_voice_queue.insert(0, queued)
            self._refresh_live_queue()
            self.live_stop_current()
            mic_active = False

        if self.live_speech_thread is None and not mic_active:
            self._live_start_next()
        return True

    def selected_soundboard_pad(self):
        item = self.soundboard_list.currentItem()
        if item is None:
            return None
        return self.soundboard_store.get_pad(item.data(Qt.ItemDataRole.UserRole))

    def trigger_selected_soundboard_pad(self):
        pad = self.selected_soundboard_pad()
        if pad is not None:
            self._queue_soundboard_pad(pad)

    def _current_context_matches_pad(self, pad):
        loaded = self.loaded_entry()
        if loaded is None:
            return False
        worker = self.active_qwen_model()
        current_style = self.qwen_instruct_input.text().strip() if worker is not None else ""
        return (
            (self.active_voice_id or "") == pad.voice_id
            and loaded["repo_id"] == pad.model_repo_id
            and loaded.get("backend", "") == pad.model_backend
            and (loaded.get("mode", "") or "") == (pad.model_mode or "")
            and (self.language_combo.currentData() or "en") == pad.language
            and current_style == pad.style
            and abs(float(self.temp_slider.get_value()) - pad.temperature) < 1e-9
            and abs(float(self.exaggeration_slider.get_value()) - pad.exaggeration) < 1e-9
            and abs(float(self.cfg_slider.get_value()) - pad.cfg_weight) < 1e-9
            and abs(float(self.repetition_penalty) - pad.repetition_penalty) < 1e-9
            and abs(float(self.min_p) - pad.min_p) < 1e-9
            and abs(float(self.top_p) - pad.top_p) < 1e-9
        )

    def _soundboard_problem(self, title, message, interactive=True):
        if interactive:
            QMessageBox.information(self, title, message)
        else:
            QApplication.beep()
            self.set_status_message(f"Status: {title}: {message}")

    def _queue_soundboard_pad(self, pad, force_generate=False, interactive=True):
        route_problem = self.live_route_problem()
        if route_problem:
            self._soundboard_problem("Soundboard route", route_problem, interactive)
            return

        if pad.kind == "audio":
            path = self.soundboard_store.audio_path(pad)
            if not path or not os.path.isfile(path):
                self._soundboard_problem(
                    "Soundboard audio",
                    f"{pad.label}'s local audio file is missing. Delete and re-import the pad.",
                    interactive,
                )
                return
            try:
                info = sf.info(path)
                clip_seconds = (
                    float(info.frames) / float(info.samplerate)
                    if info.samplerate
                    else self._estimate_live_item_seconds(pad.label)
                )
            except Exception:
                clip_seconds = self._estimate_live_item_seconds(pad.label)
            item = {
                "text": pad.label,
                "label": pad.label,
                "audio_path": path,
                "estimated_seconds": clip_seconds,
                "soundboard_pad_id": pad.id,
            }
            if self._enqueue_soundboard_item(item, pad, interactive):
                self.soundboard_status_label.setText(f"{pad.label}: audio clip")
            return

        expected = self._soundboard_cache_key(pad)
        cached = (
            not force_generate
            and pad.cache_key == expected
            and self.soundboard_store.has_cache(expected)
        )
        if cached:
            item = {
                "text": pad.text,
                "label": pad.label,
                "cached_path": self.soundboard_store.cache_path(expected),
                "cache_provenance": pad.cache_provenance,
                "soundboard_pad_id": pad.id,
            }
            if self._enqueue_soundboard_item(item, pad, interactive):
                self.soundboard_status_label.setText(f"{pad.label}: cached")
            return

        if self.live_speech_thread is None and (
            getattr(self, "model_is_loading", False) or self.is_generating or self.api_busy
        ):
            self._soundboard_problem(
                "Soundboard",
                "This pad needs to be regenerated, but the model is busy with another task.",
                interactive,
            )
            return
        if not self._current_context_matches_pad(pad):
            self._soundboard_problem(
                "Soundboard cache needs rebuilding",
                f"{pad.label} no longer has a valid cache and its saved voice/model is not active. "
                "Select that voice/model again, then trigger the pad to rebuild it.",
                interactive,
            )
            return
        item = {
            "text": pad.text,
            "label": pad.label,
            "soundboard_pad_id": pad.id,
            "cache_key": expected,
        }
        if self._enqueue_soundboard_item(item, pad, interactive):
            self.soundboard_status_label.setText(
                f"{pad.label}: generating and caching…"
            )

    def delete_selected_soundboard_pad(self):
        pad = self.selected_soundboard_pad()
        if pad is None:
            return
        self.soundboard_store.remove_pad(pad.id)
        self.refresh_soundboard_boards()
        self.refresh_soundboard()
        self.refresh_live_global_hotkeys()
        self.soundboard_status_label.setText(f"Deleted {pad.label}")

    def refresh_soundboard(self, select_id=None):
        if not hasattr(self, "soundboard_list"):
            return
        self.soundboard_list.clear()
        board = self.soundboard_store.active_board()
        if board is None:
            return

        query = (
            self.soundboard_search_input.text().strip().casefold()
            if hasattr(self, "soundboard_search_input")
            else ""
        )
        favorites_only = (
            self.soundboard_favorites_only.isChecked()
            if hasattr(self, "soundboard_favorites_only")
            else False
        )

        for index, pad in enumerate(board.pads, 1):
            searchable = " ".join(
                [pad.label, pad.text or "", " ".join(pad.tags or [])]
            ).casefold()
            if query and query not in searchable:
                continue
            if favorites_only and not pad.favorite:
                continue

            if pad.kind == "audio":
                audio_path = self.soundboard_store.audio_path(pad)
                suffix = (
                    " · audio clip"
                    if audio_path and os.path.isfile(audio_path)
                    else " · audio missing"
                )
                tooltip = audio_path or "Audio file missing"
            else:
                expected = self._soundboard_cache_key(pad)
                cached = (
                    pad.cache_key == expected
                    and self.soundboard_store.has_cache(expected)
                )
                suffix = " · cached" if cached else " · rebuild needed"
                tooltip = pad.text

            if pad.favorite:
                suffix = " ★" + suffix
            if pad.hotkey:
                suffix += f" · {pad.hotkey}"
            if pad.interrupt_policy:
                suffix += f" · {pad.interrupt_policy}"
            if index <= 9:
                suffix += f"  [Alt+{index}]"

            if pad.tags:
                tooltip = (tooltip + "\n" if tooltip else "") + "Tags: " + ", ".join(pad.tags)
            tooltip = (
                (tooltip + "\n" if tooltip else "")
                + f"Trigger behavior: {pad.interrupt_policy or 'board default'}"
            )

            self.soundboard_list.addItem(f"{pad.label}{suffix}")
            item = self.soundboard_list.item(self.soundboard_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, pad.id)
            item.setToolTip(tooltip)
            if select_id == pad.id:
                self.soundboard_list.setCurrentItem(item)

    def _assigned_live_hotkeys(self, exclude_pad_id=None, include_stop=True):
        assigned = {}
        if include_stop:
            stop = str(self.app_settings.get("live_voice", {}).get("stop_hotkey", "") or "").strip()
            if stop:
                assigned[stop] = "Global Stop All"
        board = self.soundboard_store.active_board()
        for pad in (board.pads if board is not None else []):
            if pad.id == exclude_pad_id or not pad.hotkey:
                continue
            assigned[pad.hotkey] = f"Soundboard: {pad.label}"
        return assigned

    def _prompt_live_hotkey(self, title, current=""):
        value, accepted = QInputDialog.getText(
            self,
            title,
            "Shortcut (requires Ctrl, Alt or Shift):",
            text=current or "Ctrl+Alt+1",
        )
        if not accepted:
            return ""
        try:
            return normalize_hotkey(value)
        except HotkeyError as exc:
            QMessageBox.warning(self, title, str(exc))
            return ""

    def set_selected_soundboard_hotkey(self):
        pad = self.selected_soundboard_pad()
        if pad is None:
            QMessageBox.information(self, "Soundboard hotkey", "Select a soundboard pad first.")
            return
        hotkey = self._prompt_live_hotkey("Set soundboard hotkey", pad.hotkey)
        if not hotkey:
            return
        owner = self._assigned_live_hotkeys(exclude_pad_id=pad.id).get(hotkey)
        if owner:
            QMessageBox.warning(self, "Soundboard hotkey", f"{hotkey} is already assigned to {owner}.")
            return
        pad.hotkey = hotkey
        self.soundboard_store.save()
        settings = self.app_settings.setdefault("live_voice", {})
        settings["global_hotkeys_enabled"] = True
        self.save_app_settings()
        self.live_hotkeys_enabled_checkbox.setChecked(True)
        self.refresh_soundboard(select_id=pad.id)
        self.refresh_live_global_hotkeys()

    def clear_selected_soundboard_hotkey(self):
        pad = self.selected_soundboard_pad()
        if pad is None or not pad.hotkey:
            return
        pad.hotkey = ""
        self.soundboard_store.save()
        self.refresh_soundboard(select_id=pad.id)
        self.refresh_live_global_hotkeys()

    def set_live_stop_hotkey(self):
        settings = self.app_settings.setdefault("live_voice", {})
        hotkey = self._prompt_live_hotkey("Set Global Stop All hotkey", settings.get("stop_hotkey", ""))
        if not hotkey:
            return
        owner = self._assigned_live_hotkeys(include_stop=False).get(hotkey)
        if owner:
            QMessageBox.warning(self, "Global Stop All hotkey", f"{hotkey} is already assigned to {owner}.")
            return
        settings["stop_hotkey"] = hotkey
        self.live_stop_hotkey_label.setText(hotkey)
        self.save_app_settings()
        self.refresh_live_global_hotkeys()

    def clear_live_stop_hotkey(self):
        settings = self.app_settings.setdefault("live_voice", {})
        settings["stop_hotkey"] = ""
        self.live_stop_hotkey_label.setText("Not set")
        self.save_app_settings()
        self.refresh_live_global_hotkeys()

    def on_live_global_hotkeys_toggled(self, enabled):
        settings = self.app_settings.setdefault("live_voice", {})
        settings["global_hotkeys_enabled"] = bool(enabled)
        self.save_app_settings()
        self.refresh_live_global_hotkeys()

    def refresh_live_global_hotkeys(self):
        if not hasattr(self, "live_hotkeys"):
            return
        self.live_hotkeys.clear()
        errors = []
        settings = self.app_settings.setdefault("live_voice", {})
        pad_hotkeys_enabled = bool(settings.get("global_hotkeys_enabled", False))
        stop = str(settings.get("stop_hotkey", "") or "").strip()
        if stop:
            error = self.live_hotkeys.register("stop_all", stop)
            if error:
                errors.append(f"{stop}: {error}")
        if pad_hotkeys_enabled:
            board = self.soundboard_store.active_board()
            for pad in (board.pads if board is not None else []):
                if not pad.hotkey:
                    continue
                error = self.live_hotkeys.register(f"pad:{pad.id}", pad.hotkey)
                if error:
                    errors.append(f"{pad.label} ({pad.hotkey}): {error}")

        if not self.live_hotkeys.supported:
            self.live_hotkey_status_label.setText("Global hotkeys: Windows only")
            self.live_hotkey_status_label.setToolTip("This first implementation uses the Windows RegisterHotKey API.")
        elif errors:
            self.live_hotkey_status_label.setText(
                f"Global hotkeys: {self.live_hotkeys.registered_count()} active · {len(errors)} conflict(s)"
            )
            self.live_hotkey_status_label.setToolTip("\n".join(errors))
        else:
            count = self.live_hotkeys.registered_count()
            if not pad_hotkeys_enabled:
                self.live_hotkey_status_label.setText(
                    "Global pad hotkeys: disabled"
                    + (" · Stop All active" if stop and count else "")
                )
                self.live_hotkey_status_label.setToolTip(
                    "Pad assignments are preserved but not registered. "
                    "A configured emergency Stop All remains available."
                )
            else:
                self.live_hotkey_status_label.setText(
                    f"Global hotkeys: {count} active" if count else "Global hotkeys: none"
                )
                self.live_hotkey_status_label.setToolTip("")

    def handle_live_global_hotkey(self, action_id):
        if action_id == "stop_all":
            self.live_stop_all()
            return
        if str(action_id).startswith("pad:"):
            pad = self.soundboard_store.get_pad(str(action_id).split(":", 1)[1])
            if pad is not None:
                self._queue_soundboard_pad(pad, interactive=False)

    def _refresh_live_queue(self):
        self.live_queue_list.clear()
        queued_seconds = 0.0
        for index, item in enumerate(self.live_voice_queue, 1):
            text = str(item.get("text") or "").replace("\n", " ")
            queued_seconds += float(
                item.get("estimated_seconds")
                or self._estimate_live_item_seconds(text)
            )
            self.live_queue_list.addItem(f"{index}. {text[:120]}")
        if hasattr(self, "live_queue_pressure_label"):
            self.live_queue_pressure_label.setText(
                f"{len(self.live_voice_queue)} queued · ~{queued_seconds:.0f}s"
            )

    def _set_live_generation_busy(self, busy):
        self.live_voice_busy = bool(busy)
        mic_active = (
            hasattr(self, "live_mic_input") and self.live_mic_input.is_active()
        )
        audible = self.live_audio_output.is_playing() or self.live_monitor_output.is_playing()
        self.live_stop_current_button.setEnabled(busy or audible or mic_active)
        self.live_stop_all_button.setEnabled(
            busy or audible or mic_active or bool(self.live_voice_queue)
        )
        self.live_output_combo.setEnabled(not busy)
        self.live_route_profile_combo.setEnabled(not busy)
        self.live_arm_button.setEnabled(not busy)
        self.live_route_test_button.setEnabled(not busy)
        self.live_route_setup_button.setEnabled(not busy)
        self.live_monitor_checkbox.setEnabled(not busy)
        self.live_monitor_combo.setEnabled(not busy and self.live_monitor_checkbox.isChecked())
        if hasattr(self, "live_source_mode_combo"):
            self.live_source_mode_combo.setEnabled(not busy)
        if hasattr(self, "live_mic_input_combo"):
            self.live_mic_input_combo.setEnabled(not busy)
            self.live_mic_effects_enabled.setEnabled(not busy)
            self.live_mic_gain.setEnabled(not busy)
            self.live_mic_tone.setEnabled(not busy)
            self.live_mic_compressor.setEnabled(not busy)
            self.live_mic_start_button.setEnabled(
                mic_active or (
                    not busy
                    and self.live_source_mode_combo.currentData() == "mic_effects"
                )
            )
        tts_mode = (
            not hasattr(self, "live_source_mode_combo")
            or self.live_source_mode_combo.currentData() == "tts"
        )
        if hasattr(self, "live_text_input"):
            self.live_text_input.setEnabled(not busy and tts_mode)
        if hasattr(self, "live_speak_button"):
            self.live_speak_button.setEnabled(not busy and tts_mode)
        other_busy = getattr(self, "model_is_loading", False) or self.api_busy
        self.model_repo_combo.setEnabled(not busy and not other_busy)
        self.generate_button.setEnabled(not busy and not other_busy and self.model is not None)
        self.preview_button.setEnabled(not busy and not other_busy and self.model is not None)

    def _refresh_live_stop_buttons(self):
        mic_active = (
            hasattr(self, "live_mic_input") and self.live_mic_input.is_active()
        )
        audible = self.live_audio_output.is_playing() or self.live_monitor_output.is_playing()
        self.live_stop_current_button.setEnabled(
            self.live_voice_busy or audible or mic_active
        )
        self.live_stop_all_button.setEnabled(
            self.live_voice_busy or audible or mic_active or bool(self.live_voice_queue)
        )
        return audible or mic_active

    def on_live_buffer_changed(self, milliseconds):
        audible = self._refresh_live_stop_buttons() or milliseconds > 1.0
        if audible:
            if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
                self.live_status_label.setText(
                    f"Mic Effects live · {milliseconds / 1000.0:.2f}s buffered"
                )
            else:
                self.live_status_label.setText(
                    f"Speaking · {milliseconds / 1000.0:.2f}s buffered"
                )

    def on_live_audio_drained(self):
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            self._refresh_live_stop_buttons()
            self.live_current_label.setText("Microphone effects active.")
            self.live_status_label.setText("Mic Effects live")
            return
        if self.live_speech_thread is None and not self.live_voice_queue:
            if self.live_audio_output.is_playing() or self.live_monitor_output.is_playing():
                self._refresh_live_stop_buttons()
                self.live_current_label.setText("Finishing playback…")
                return
            self.live_stop_current_button.setEnabled(False)
            self.live_stop_all_button.setEnabled(False)
            self.live_current_label.setText("Nothing speaking.")
            self.live_status_label.setText("Ready")

    def on_live_monitor_error(self, message):
        self.live_monitor_output.stop()
        self.live_monitor_status.setText(f"Monitor stopped: {message}")
        self.set_status_message(
            f"Status: Live Voice monitor stopped, but the primary route is still active: {message}"
        )
        self._refresh_live_stop_buttons()

    def on_live_audio_error(self, message):
        self.live_voice_last_error = str(message)
        if hasattr(self, "live_mic_input") and self.live_mic_input.is_active():
            self.stop_live_microphone()
        self.live_voice_queue.clear()
        self._refresh_live_queue()
        self.live_audio_output.stop()
        self.live_monitor_output.stop()
        if self.live_speech_thread is not None:
            self.live_speech_thread.stop()
        self.live_status_label.setText(f"Audio error: {message}")
        self.set_status_message(f"Status: Live Voice audio error: {message}")

    def on_live_speech_error(self, message):
        self.live_voice_last_error = str(message)
        self.live_voice_queue.clear()
        self._refresh_live_queue()
        self.live_audio_output.stop()
        self.live_monitor_output.stop()
        self.live_status_label.setText(f"Speech error: {message}")
        self.set_status_message(f"Status: Live Voice generation error: {message}")
