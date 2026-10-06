"""Live Voice page: type text and send generated PCM directly to an audio device."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from this_voice_thing.core.live_voice import LiveSpeechSession
from this_voice_thing.engines import vibevoice as vibevoice_engine
from this_voice_thing.ui.live_audio import LiveAudioOutput, LiveSpeechThread
from this_voice_thing.ui import theme as ui_theme


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
        self.live_audio_devices = []

        self.live_audio_output = LiveAudioOutput(self)
        self.live_audio_output.failed.connect(self.on_live_audio_error)
        self.live_audio_output.drained.connect(self.on_live_audio_drained)
        self.live_audio_output.buffer_changed.connect(self.on_live_buffer_changed)

        page, layout = self._make_page(
            "Live Voice",
            "Type, speak, queue, and route the current voice directly to an audio device.",
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
        route_layout.addLayout(voice_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Send voice to"))
        self.live_output_combo = QComboBox()
        self.live_output_combo.setMinimumWidth(320)
        self.live_output_combo.currentIndexChanged.connect(self.on_live_output_changed)
        output_row.addWidget(self.live_output_combo, 1)
        refresh = self._link(QPushButton("Refresh devices"))
        refresh.clicked.connect(self.refresh_live_audio_devices)
        output_row.addWidget(refresh)
        self.live_route_state = QLabel("LOCAL OUTPUT")
        self.live_route_state.setObjectName("Muted")
        output_row.addWidget(self.live_route_state)
        route_layout.addLayout(output_row)
        layout.addWidget(route_card)

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
        clear = self._link(QPushButton("Clear queued"))
        clear.clicked.connect(self.live_clear_queue)
        queue_actions.addWidget(clear)
        queue_actions.addStretch(1)
        queue_layout.addLayout(queue_actions)
        layout.addWidget(queue_card, 1)

        QShortcut(QKeySequence("Ctrl+Return"), page, activated=self.live_submit)
        QShortcut(QKeySequence("Ctrl+Enter"), page, activated=self.live_submit)

        self.pages.addWidget(page)
        try:
            self.media_devices.audioOutputsChanged.connect(self.refresh_live_audio_devices)
        except Exception:
            pass
        self.refresh_live_audio_devices()
        self.refresh_live_voice_summary()

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
        if self.model is None:
            mode = "Unavailable"
        elif getattr(self.model, "native_streaming", False):
            mode = "Native streaming"
        elif getattr(self.model, "segmented_streaming", False):
            mode = "Segmented streaming"
        else:
            mode = "Buffered fallback"
        self.live_mode_label.setText(mode)

    @staticmethod
    def _device_id_hex(device):
        return LiveAudioOutput.device_key(device).hex()

    def refresh_live_audio_devices(self):
        if not hasattr(self, "live_output_combo"):
            return
        saved = self.app_settings.get("live_voice", {}).get("output_device_id", "")
        current = self.live_output_combo.currentData()
        if current is not None and 0 <= int(current) < len(self.live_audio_devices):
            saved = self._device_id_hex(self.live_audio_devices[int(current)])

        self.live_audio_devices = list(self.media_devices.audioOutputs())
        self.live_output_combo.blockSignals(True)
        self.live_output_combo.clear()

        chosen = -1
        if saved:
            for index, device in enumerate(self.live_audio_devices):
                if self._device_id_hex(device) == saved:
                    chosen = index
                    break

        missing_saved_device = bool(saved) and chosen < 0
        if missing_saved_device:
            saved_name = self.app_settings.get("live_voice", {}).get("output_device_name", "saved output")
            self.live_output_combo.addItem(f"Unavailable: {saved_name} — choose another output", None)

        for index, device in enumerate(self.live_audio_devices):
            self.live_output_combo.addItem(device.description(), index)

        if not saved and self.live_audio_devices:
            default_key = self._device_id_hex(self.media_devices.defaultAudioOutput())
            for index, device in enumerate(self.live_audio_devices):
                if self._device_id_hex(device) == default_key:
                    chosen = index
                    break
            if chosen < 0:
                chosen = 0

        if missing_saved_device:
            self.live_output_combo.setCurrentIndex(0)
        elif chosen >= 0:
            self.live_output_combo.setCurrentIndex(chosen)
        self.live_output_combo.blockSignals(False)
        self.live_speak_button.setEnabled(self.current_live_audio_device() is not None)
        if missing_saved_device:
            self.live_status_label.setText("Saved audio output is unavailable. Choose a new destination.")

    def current_live_audio_device(self):
        index = self.live_output_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self.live_audio_devices)):
            return None
        return self.live_audio_devices[index]

    def on_live_output_changed(self, _index):
        device = self.current_live_audio_device()
        if device is None:
            return
        settings = self.app_settings.setdefault("live_voice", {})
        settings["output_device_id"] = self._device_id_hex(device)
        settings["output_device_name"] = device.description()
        self.save_app_settings()

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
        if self.current_live_audio_device() is None:
            QMessageBox.information(self, "Live Voice", "Choose an audio output device first.")
            return

        self.live_voice_queue.append({"text": text})
        self.live_text_input.clear()
        self._refresh_live_queue()
        self.live_stop_all_requested = False
        if self.live_speech_thread is None:
            self._live_start_next()

    def _live_start_next(self):
        if self.live_speech_thread is not None:
            return
        if not self.live_voice_queue:
            self.live_voice_current = None
            self.live_current_label.setText("Nothing speaking.")
            self.live_audio_output.finish_input()
            self._set_live_generation_busy(False)
            return

        item = self.live_voice_queue.pop(0)
        self.live_voice_current = item
        self.live_voice_last_error = ""
        self._refresh_live_queue()
        try:
            session = self._make_live_session(item["text"])
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
            self.on_live_audio_error("The selected audio output disappeared.")
            return
        try:
            self.live_audio_output.configure(device, int(meta["sample_rate"]))
        except Exception as exc:
            self.on_live_audio_error(str(exc))
            return
        mode = str(meta.get("mode") or "buffered")
        self.live_mode_label.setText(
            {"native": "Native streaming", "segmented": "Segmented streaming"}.get(
                mode, "Buffered fallback"
            )
        )
        self.live_status_label.setText(
            f"Generating · {mode} · {device.description()}"
        )

    def on_live_frame(self, frame):
        try:
            self.live_audio_output.push(frame.pcm)
        except Exception as exc:
            self.on_live_audio_error(str(exc))

    def on_live_session_complete(self, metrics):
        if not metrics.get("cancelled"):
            self.live_voice_history.append({
                "text": self.live_voice_current["text"] if self.live_voice_current else "",
                "metrics": dict(metrics),
            })
            self.live_voice_history = self.live_voice_history[-50:]
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
            self._set_live_generation_busy(False)
            self.live_current_label.setText("Finishing playback…")

    def live_stop_current(self):
        if self.live_speech_thread is None and not self.live_audio_output.is_playing():
            return
        self.live_audio_output.stop()
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
        self.live_audio_output.stop()
        if self.live_speech_thread is not None:
            self.live_speech_thread.stop()
            self.live_status_label.setText("Stopping…")
        else:
            self._set_live_generation_busy(False)
            self.live_status_label.setText("Stopped")
            self.live_current_label.setText("Nothing speaking.")

    def live_clear_queue(self):
        self.live_voice_queue.clear()
        self._refresh_live_queue()

    def live_repeat_last(self):
        if not self.live_voice_history:
            return
        self.live_text_input.setPlainText(self.live_voice_history[-1]["text"])
        self.live_submit()

    def _refresh_live_queue(self):
        self.live_queue_list.clear()
        for index, item in enumerate(self.live_voice_queue, 1):
            text = item["text"].replace("\n", " ")
            self.live_queue_list.addItem(f"{index}. {text[:120]}")

    def _set_live_generation_busy(self, busy):
        self.live_voice_busy = bool(busy)
        audible = self.live_audio_output.is_playing()
        self.live_stop_current_button.setEnabled(busy or audible)
        self.live_stop_all_button.setEnabled(busy or audible or bool(self.live_voice_queue))
        self.live_output_combo.setEnabled(not busy)
        other_busy = getattr(self, "model_is_loading", False) or self.api_busy
        self.model_repo_combo.setEnabled(not busy and not other_busy)
        self.generate_button.setEnabled(not busy and not other_busy and self.model is not None)
        self.preview_button.setEnabled(not busy and not other_busy and self.model is not None)

    def on_live_buffer_changed(self, milliseconds):
        audible = self.live_audio_output.is_playing() or milliseconds > 1.0
        self.live_stop_current_button.setEnabled(self.live_voice_busy or audible)
        self.live_stop_all_button.setEnabled(
            self.live_voice_busy or audible or bool(self.live_voice_queue)
        )
        if audible:
            self.live_status_label.setText(
                f"Speaking · {milliseconds / 1000.0:.2f}s buffered"
            )

    def on_live_audio_drained(self):
        if self.live_speech_thread is None and not self.live_voice_queue:
            self.live_stop_current_button.setEnabled(False)
            self.live_stop_all_button.setEnabled(False)
            self.live_current_label.setText("Nothing speaking.")
            self.live_status_label.setText("Ready")

    def on_live_audio_error(self, message):
        self.live_voice_last_error = str(message)
        self.live_voice_queue.clear()
        self._refresh_live_queue()
        self.live_audio_output.stop()
        if self.live_speech_thread is not None:
            self.live_speech_thread.stop()
        self.live_status_label.setText(f"Audio error: {message}")
        self.set_status_message(f"Status: Live Voice audio error: {message}")

    def on_live_speech_error(self, message):
        self.live_voice_last_error = str(message)
        self.live_voice_queue.clear()
        self._refresh_live_queue()
        self.live_audio_output.stop()
        self.live_status_label.setText(f"Speech error: {message}")
        self.set_status_message(f"Status: Live Voice generation error: {message}")
