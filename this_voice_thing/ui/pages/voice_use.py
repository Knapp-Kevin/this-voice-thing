"""Using a saved voice: loading the model it needs, applying it, and making clips of preset or designed voices."""

import os

import numpy as np
from PySide6.QtWidgets import QMessageBox

from this_voice_thing.core import model_registry
from this_voice_thing.core import voice_library
from this_voice_thing.engines import kokoro as kokoro_engine
from this_voice_thing.ui.common import DUAL_MODE_BACKENDS
from this_voice_thing.ui.common import REFERENCE_READING_SCRIPTS
from this_voice_thing.ui.threads import MakeClipThread


class VoiceUse:
    """Putting a saved voice to use. Mixed into ChatterboxApp."""

    def entry_for_voice(self, voice):
        for entry in self.model_entries:
            if entry.get("backend") != voice.backend or entry.get("repo_id") != voice.repo_id:
                continue
            if voice.backend in DUAL_MODE_BACKENDS and model_registry.entry_mode(entry) != (voice.mode or "design"):
                continue
            return entry
        return None

    def use_voice(self, voice, as_clip=False):
        path = self.voice_library.clip_path(voice)
        if voice.kind == "clip" or as_clip:
            if not os.path.exists(path):
                QMessageBox.warning(self, "Voice", f"The clip for {voice.name} is missing:\n{path}")
                return
            if not self.model_can_clone():
                self.load_for_clip_voice(voice)  # applied once a cloning model is ready
                return
            self.set_reference_audio(path)
            self.active_voice_id = voice.id
            self.remember_clone_entry()
            self.refresh_model_repo_options()
            self.set_status_message(f"Status: Voice set to {voice.name}.")
            self.render_voice_tiles()
            return
        entry = self.entry_for_voice(voice)
        if entry is None:
            engine = model_registry.ENGINES.get(voice.backend)
            QMessageBox.information(
                self, "Voice", f"{voice.name} needs {engine.label if engine else voice.backend} "
                f"({voice.repo_id}), which isn't in your model list. Add it on the Model page first.")
            return
        if self.model_busy():
            self.set_status_message("Status: Wait for the current load or generation to finish.")
            return
        self.pending_voice = voice
        if self.is_active_entry(entry):
            self.apply_pending_voice()
        else:
            self.set_status_message(f"Status: Loading {entry['label']} for {voice.name}...")
            self.load_entry(entry)

    def apply_pending_voice(self):
        voice, self.pending_voice = self.pending_voice, None
        model = self.active_qwen_model()
        if voice is None or model is None or getattr(model, "backend", "") != voice.backend:
            return
        if voice.language:
            index = self.language_combo.findData(voice.language)
            if index >= 0:
                self.language_combo.setCurrentIndex(index)
        if voice.kind == "preset":
            if isinstance(model, kokoro_engine.KokoroModel):
                language = voice.language or kokoro_engine.voice_language(voice.speaker) or "en"
                self.kokoro_settings.setdefault("voice_by_language", {})[language] = voice.speaker
            else:
                self.qwen_settings.update(speaker=voice.speaker, style=voice.style)
        else:
            self.engine_settings(model)["description"] = voice.description
            path = self.voice_library.clip_path(voice)
            if hasattr(model, "locked_anchor"):
                model.locked_anchor = None
                self.locked_voice_name = None
            if voice.has_clip and os.path.exists(path) and hasattr(model, "locked_anchor"):
                model.locked_anchor = (path, voice_library.read_transcript(path))
                self.locked_voice_name = voice.name
        self.update_engine_controls()
        if voice.kind == "preset":
            index = self.qwen_speaker_combo.findData(voice.speaker)
            if index >= 0:
                self.qwen_speaker_combo.setCurrentIndex(index)
        self.active_voice_id = voice.id
        self.set_status_message(f"Status: Voice set to {voice.name}.")
        self.refresh_voice_chip()
        self.render_voice_tiles()

    def make_voice_clip(self, voice):
        """Generate a clip of a preset or designed voice reading a passage, so cloning
        models can use it. The voice has to be the one in use (it is loaded first)."""
        if not self.voice_is_active(voice):
            self.use_voice(voice)
            if not self.voice_is_active(voice):
                self.pending_clip_voice = voice  # made once the model has loaded
                return
        problem = self.prepare_qwen_generation()
        if problem:
            QMessageBox.information(self, "Make Clip", problem)
            return
        language = self.language_combo.currentData() or "en"
        passage = REFERENCE_READING_SCRIPTS[0]
        self.set_status_message(f"Status: Making a clip of {voice.name}...")
        self.generate_button.setEnabled(False)
        self.clip_thread = MakeClipThread(self.model, passage, language, self)
        self.clip_thread.finished_with.connect(
            lambda wav, sr, error, v=voice, text=passage: self.on_voice_clip_made(v, text, wav, sr, error))
        self.clip_thread.start()

    def on_voice_clip_made(self, voice, text, wav, sr, error):
        self.generate_button.setEnabled(self.model is not None)
        if error or wav is None:
            self.set_status_message("Status: Could not make the clip. See the Log page.")
            QMessageBox.warning(self, "Make Clip", f"Could not make the clip:\n{error}")
            return
        old = self.voice_library.clip_path(voice)
        path = self.voice_library.new_clip_path(voice.name)
        import soundfile
        soundfile.write(path, np.clip(wav, -1.0, 1.0), sr, subtype="PCM_16")
        voice_library.write_transcript(path, text)
        voice.clip = self.voice_library.to_stored(path)
        self.voice_library.save()
        if old and old != path and old.startswith(os.path.abspath(self.voice_library.clips_dir)):
            for target in (old, voice_library.transcript_path(old)):
                if os.path.exists(target):
                    os.remove(target)
        self.set_status_message(f"Status: Made a {len(wav) / sr:.0f} s clip of {voice.name}. "
                                "Cloning models can use it now.")
        self.render_voice_tiles()
