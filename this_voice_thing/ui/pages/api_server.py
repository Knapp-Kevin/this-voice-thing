"""The local API: its settings card, the server, and answering requests with the app's models and voices."""

import os
import tempfile

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSpinBox

from this_voice_thing.core import audio_effects
from this_voice_thing.core import documents
from this_voice_thing.core import model_registry
from this_voice_thing.core import voice_library
from this_voice_thing.engines import vibevoice as vibevoice_engine
from this_voice_thing.engines import voxcpm as voxcpm_engine
from this_voice_thing.integrations import local_api
from this_voice_thing.ui.api_bridge import ApiBridge


class ApiServer:
    """The local HTTP API. Mixed into ChatterboxApp."""

    def _build_api_card(self):
        api_card, api_layout = self._make_card("Local API")
        api_top = QHBoxLayout()
        self.api_checkbox = QCheckBox("Let other programs on this PC use the app")
        self.api_checkbox.setToolTip(
            "Starts a small web server on 127.0.0.1 (this computer only). Scripts and tools that speak "
            "the OpenAI speech API, such as Open WebUI or SillyTavern, can then use your models and voices.")
        self.api_checkbox.setChecked(bool(self.api_settings.get("enabled")))
        self.api_checkbox.toggled.connect(self.on_api_toggled)
        api_top.addWidget(self.api_checkbox)
        api_top.addStretch(1)
        self.api_status_label = QLabel()
        self.api_status_label.setObjectName("Muted")
        self.api_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        api_top.addWidget(self.api_status_label)
        api_layout.addLayout(api_top)
        api_row = QHBoxLayout()
        api_row.addWidget(QLabel("Port"))
        self.api_port_spin = QSpinBox()
        self.api_port_spin.setRange(1024, 65535)
        self.api_port_spin.setValue(int(self.api_settings.get("port") or local_api.DEFAULT_PORT))
        self.api_port_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.api_port_spin.setFixedWidth(70)
        self.api_port_spin.editingFinished.connect(self.on_api_settings_changed)
        api_row.addWidget(self.api_port_spin)
        api_row.addWidget(QLabel("Token"))
        self.api_token_input = QLineEdit(str(self.api_settings.get("token") or ""))
        self.api_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_token_input.setPlaceholderText("Optional: required as a Bearer token")
        self.api_token_input.editingFinished.connect(self.on_api_settings_changed)
        api_row.addWidget(self.api_token_input, 1)
        copy_example = self._link(QPushButton("Copy example"))
        copy_example.setToolTip("Copy a curl command that saves speech to speech.mp3.")
        copy_example.clicked.connect(self.copy_api_example)
        api_row.addWidget(copy_example)
        api_layout.addLayout(api_row)
        return api_card

    # --- Local API ---

    def save_api_settings(self):
        self.api_settings.update(enabled=self.api_checkbox.isChecked(), port=self.api_port_spin.value(),
                                 token=self.api_token_input.text().strip())
        self.app_settings["api"] = dict(self.api_settings)
        self.save_app_settings()

    def on_api_toggled(self, checked):
        self.save_api_settings()
        self.restart_api_server()

    def on_api_settings_changed(self):
        changed = (self.api_port_spin.value() != self.api_settings.get("port")
                   or self.api_token_input.text().strip() != self.api_settings.get("token"))
        self.save_api_settings()
        if changed and self.api_checkbox.isChecked():
            self.restart_api_server()

    def restart_api_server(self):
        if self.api_server is not None:
            self.api_server.stop()
            self.api_server = None
        if self.api_checkbox.isChecked():
            server = local_api.LocalApiServer(self.api_bridge, self.api_port_spin.value(),
                                              self.api_token_input.text(), log=print)
            try:
                server.start()
            except OSError as exc:
                self.api_status_label.setText(f"Port {self.api_port_spin.value()} is in use")
                self.api_status_label.setToolTip(str(exc))
                print(f"Local API could not start: {exc}")
                return
            self.api_server = server
            self.api_status_label.setText(f"On: {server.url}")
            self.api_status_label.setToolTip("Only programs on this computer can connect.")
        else:
            self.api_status_label.setText("Off")
            self.api_status_label.setToolTip("")

    def copy_api_example(self):
        url = f"http://127.0.0.1:{self.api_port_spin.value()}"
        token = self.api_token_input.text().strip()
        auth = f' -H "Authorization: Bearer {token}"' if token else ""
        command = (f'curl {url}/v1/audio/speech -H "Content-Type: application/json"{auth} '
                   '-d "{\\"input\\": \\"Hello from my own computer.\\", \\"response_format\\": \\"mp3\\"}" '
                   "-o speech.mp3")
        QApplication.clipboard().setText(command)
        self.set_status_message("Status: Copied an example curl command.")

    def api_health(self):
        entry = self.loaded_entry() if self.model is not None else None
        return {
            "status": "busy" if self.model_busy() else "ready" if self.model is not None else "no model loaded",
            "model": entry["label"] if entry else None,
            "device": self.device_used if self.model is not None else None,
            "voice": self.voice_chip.text(),
            "endpoints": ["GET /v1/health", "GET /v1/models", "GET /v1/voices",
                          "POST /v1/audio/speech (OpenAI-compatible)", "POST /v1/speech"],
        }

    def api_models(self):
        rows = []
        for entry in self.model_entries:
            rows.append({
                "id": entry["label"], "object": "model", "repo_id": entry["repo_id"],
                "engine": model_registry.engine_label(entry),
                "capability": model_registry.capability_for(entry),
                "streaming": model_registry.live_audio_capability(entry),
                "loaded": self.is_active_entry(entry), "downloaded": model_registry.is_downloaded(entry),
                "installed": self.engine_installed(entry),
            })
        return rows

    def api_voices(self):
        rows = [{"id": voice.id, "name": voice.name, "kind": voice.kind, "tags": voice.tags,
                 "engine": voice.backend or "any cloning model", "has_clip": voice.has_clip}
                for voice in self.voice_library.voices]
        model = self.active_qwen_model()
        for speaker in getattr(model, "speakers", []) or []:
            rows.append({"id": speaker, "name": speaker, "kind": "built-in",
                         "engine": getattr(model, "backend", "")})
        for name in (getattr(model, "sample_paths", None) or {}):
            rows.append({"id": name.split(" (")[0], "name": name, "kind": "sample", "engine": "vibevoice"})
        return rows

    def api_find_entry(self, name):
        name = str(name or "").strip()
        if name.lower() in ApiBridge.OPENAI_MODEL_NAMES:
            return None
        for entry in self.model_entries:
            if name.lower() in (entry["label"].lower(), entry["repo_id"].lower()):
                return entry
        raise local_api.ApiError(404, f"No model called {name!r}. See GET /v1/models.", "not_found")

    def api_begin(self, request):
        """Resolve a request's model and voice, and claim the GPU. Runs on the UI thread.
        Returns {"loading": True} while a model it asked for is still loading."""
        if getattr(self, "model_is_loading", False):
            if self.api_loading_entry is not None:
                return {"loading": True}
            raise local_api.ApiError(503, "A model is loading in the app; try again shortly.", "server_error")
        self.api_loading_entry = None
        if self.is_generating or self.api_busy or getattr(self, "live_voice_busy", False):
            raise local_api.ApiError(503, "The app is generating right now; try again shortly.", "server_error")
        entry = self.api_find_entry(request.get("model"))
        voice = self.api_find_voice(request.get("voice"))
        if voice is not None and voice.kind != "clip" and entry is None:
            entry = self.entry_for_voice(voice)  # a preset or designed voice brings its model
        if entry is not None and not self.is_active_entry(entry):
            if not self.engine_installed(entry):
                raise local_api.ApiError(409, f"{entry['label']} needs its engine installed; load it once in "
                                              "the app first.")
            self.api_loading_entry = entry
            self.set_status_message(f"Status: Loading {entry['label']} for an API request...")
            self.load_entry(entry)
            return {"loading": True}
        if self.model is None:
            raise local_api.ApiError(409, "No model is loaded in the app.")
        model = self.model
        text = request["text"]
        reference = self.reference_path or None
        voice_label = self.voice_chip.text()
        worker = self.active_qwen_model()
        if worker is not None:
            problem = self.prepare_qwen_generation()  # the app's current voice settings first...
            if problem and voice is None and not request.get("style"):
                raise local_api.ApiError(400, problem)
        if voice is not None:  # ...then the requested voice on top
            reference, voice_label = self.api_apply_voice(voice, reference)
        elif request.get("voice") and worker is not None:
            voice_label = self.api_apply_speaker(str(request["voice"]))
        elif request.get("voice") and str(request["voice"]).lower() not in self.OPENAI_VOICES | {"default"}:
            raise local_api.ApiError(404, f"No voice called {request['voice']!r}. See GET /v1/voices.", "not_found")
        style = str(request.get("style") or "").strip()
        if style and worker is not None and worker.mode in ("custom_voice", "voice_design") or (
                style and isinstance(worker, voxcpm_engine.VoxCPMModel)):
            worker.instruct = style
        if isinstance(worker, vibevoice_engine.VibeVoiceModel):
            speakers = documents.script_speakers(documents.parse_script(text))
            if len(speakers) > vibevoice_engine.MAX_SPEAKERS:
                raise local_api.ApiError(400, f"VibeVoice handles up to {vibevoice_engine.MAX_SPEAKERS} speakers.")
            worker.cast = self.current_cast(speakers)
        if worker is not None and hasattr(worker, "begin_run"):
            worker.begin_run()
        if worker is not None and worker.mode == "voice_design" and not worker.instruct.strip():
            raise local_api.ApiError(400, "This is a voice design model: give a designed voice or a \"style\" "
                                          "(the voice description).")
        if worker is not None and worker.mode == "base" and not reference:
            raise local_api.ApiError(400, "This cloning model needs a voice: name a clip voice, or pick one in the app.")
        finishing = self.current_finishing_settings()
        output_format = str(request.get("format") or "WAV").upper()
        if output_format not in audio_effects.OUTPUT_FORMATS:
            raise local_api.ApiError(400, f"format must be one of {', '.join(audio_effects.OUTPUT_FORMATS)}.")
        finishing.output_format = output_format
        if request.get("speed") not in (None, ""):
            try:
                finishing.speed = float(np.clip(float(request["speed"]), *audio_effects.SPEED_RANGE))
            except (TypeError, ValueError):
                raise local_api.ApiError(400, "speed must be a number.")
        subtitle_format = str(request.get("subtitles") or "").strip().lower()
        finishing.save_subtitles = subtitle_format in ("srt", "vtt", "webvtt")
        finishing.subtitle_format = "WebVTT" if subtitle_format in ("vtt", "webvtt") else "SRT"
        language = request.get("language") or self.language_combo.currentData() or "en"
        if request.get("save"):
            output_dir = os.path.join(self.output_directory, "api")
            os.makedirs(output_dir, exist_ok=True)
        else:
            output_dir = tempfile.mkdtemp(prefix="tts_api_")
        name = documents.safe_file_stem(str(request.get("name") or "api")) if request.get("save") else "api"
        self.api_busy = True
        self.generate_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.model_repo_combo.setEnabled(False)
        self.set_status_message("Status: Generating for a local API request...")
        loaded = self.loaded_entry()
        return {
            "generator": dict(
                model=model, text=text, audio_prompt_path=reference,
                exaggeration=self.exaggeration_slider.get_value(), temperature=self.temp_slider.get_value(),
                cfg_weight=self.cfg_slider.get_value(), seed=0, output_dir=output_dir, language_id=language,
                repetition_penalty=self.repetition_penalty, min_p=self.min_p, top_p=self.top_p,
                finishing=finishing, output_name=name, preview=False),
            "pronunciations": self.pronunciations,
            "model_label": loaded["label"] if loaded else None,
            "voice_label": voice_label,
            "mime": {"WAV": "audio/wav", "FLAC": "audio/flac", "MP3": "audio/mpeg"}.get(output_format, "audio/wav"),
            "temporary": not request.get("save"),
        }

    def api_find_voice(self, name):
        name = str(name or "").strip()
        if not name:
            return None
        lowered = name.lower()
        for voice in self.voice_library.voices:
            if lowered in (voice.id.lower(), voice.name.lower()):
                return voice
        return None  # maybe a built-in speaker of the loaded model (handled later)

    def api_apply_voice(self, voice, reference):
        """Point the loaded model at a library voice; returns (reference clip, label)."""
        model = self.active_qwen_model()
        path = self.voice_library.clip_path(voice)
        if voice.kind == "clip" or (model is None or model.mode == "base"):
            if not voice.has_clip or not os.path.exists(path):
                raise local_api.ApiError(400, f"{voice.name} has no clip; this model clones from a clip.")
            if model is not None:
                model.ref_text = voice_library.read_transcript(path)
            return path, voice.name
        if voice.kind == "preset" and model.mode in ("custom_voice", "preset"):
            model.speaker = voice.speaker
            if model.mode == "custom_voice":
                model.instruct = voice.style
        elif voice.kind == "design" and model.mode == "voice_design":
            model.instruct = voice.description
        else:
            raise local_api.ApiError(400, f"{voice.name} doesn't fit the loaded model.")
        return reference, voice.name

    def api_apply_speaker(self, name):
        """A built-in speaker of the loaded model (Kokoro/Qwen), by id or plain name."""
        model = self.active_qwen_model()
        speakers = getattr(model, "speakers", []) or []
        lowered = name.lower()
        match = next((speaker for speaker in speakers if speaker.lower() == lowered), None)
        if match is None:  # "alloy" -> Kokoro's af_alloy, "heart" -> af_heart
            match = next((speaker for speaker in speakers if speaker.lower().split("_", 1)[-1] == lowered), None)
        if match is not None:
            model.speaker = match
            return match
        if lowered in self.OPENAI_VOICES:
            return self.voice_chip.text()  # no such voice here: use the app's current voice
        raise local_api.ApiError(404, f"No voice called {name!r}. See GET /v1/voices.", "not_found")

    def api_end(self):
        self.api_busy = False
        has_model = self.model is not None
        self.generate_button.setEnabled(has_model)
        self.preview_button.setEnabled(has_model)
        self.model_repo_combo.setEnabled(not getattr(self, "model_is_loading", False))
        self.set_status_message("Status: Finished a local API request. Ready.")
