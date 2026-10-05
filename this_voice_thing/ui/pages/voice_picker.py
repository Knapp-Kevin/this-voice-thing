"""Voice first: Generate picks a voice (saved, or built into a model), and the model list
shows only the models that can speak it."""

import os

from PySide6.QtWidgets import QMenu, QMessageBox

from this_voice_thing.core import model_registry
from this_voice_thing.ui.common import BACKEND_LEGACY, BACKEND_MULTILINGUAL, DEFAULT_MODEL_REPO

CHATTERBOX_BACKENDS = (BACKEND_MULTILINGUAL, BACKEND_LEGACY)
BUILT_IN_TITLES = {"preset": "{label}", "conversation": "{label} (a cast of voices)"}


class VoicePicker:
    """The voice picker on Generate and the voice-to-model fit. Mixed into ChatterboxApp."""

    def _build_voice_menu(self):
        self.voice_menu = QMenu(self)
        self.voice_menu.aboutToShow.connect(self.fill_voice_menu)
        return self.voice_menu

    # ---------- what's in use, and which models fit it ----------

    def generate_voice_kind(self):
        """"cast", "preset", "design", "clip" or "default": what Generate speaks with now."""
        model = self.active_qwen_model()
        mode = getattr(model, "mode", "") if model is not None else ""
        if mode == "conversation":
            return "cast"
        if mode in ("custom_voice", "preset"):
            return "preset"
        if mode == "voice_design":
            return "design"
        return "clip" if self.reference_path else "default"

    def entry_fits_voice(self, entry):
        """Can this model speak the voice in use? (The loaded model always fits.)"""
        if self.model is not None and self.is_active_entry(entry):
            return True
        capability = model_registry.capability_for(entry)
        kind = self.generate_voice_kind()
        if kind in ("clip", "design"):
            # A clip, or a designed voice frozen as a clip: every cloning model can say it.
            return capability == "clone"
        if kind == "preset":
            return capability == "preset" and entry.get("backend") == getattr(self.model, "backend", None)
        if kind == "cast":
            return capability == "conversation"
        return entry.get("backend") in CHATTERBOX_BACKENDS  # Chatterbox's built-in voice

    def model_can_clone(self):
        model = self.model
        if model is None:
            return False
        mode = getattr(model, "mode", None)
        return mode is None or mode == "base"  # Chatterbox has no mode; worker cloning models are "base"

    def preferred_clone_entry(self):
        """The cloning model to load for a clip voice: the last one used, else Chatterbox."""
        entries = [entry for entry in self.get_visible_model_entries()
                   if model_registry.capability_for(entry) == "clone"]
        last = self.app_settings.get("last_clone_entry")
        for entry in entries:
            if self.entry_key(entry) == last:
                return entry
        for entry in entries:
            if entry["repo_id"] == DEFAULT_MODEL_REPO and entry.get("backend") == BACKEND_MULTILINGUAL:
                return entry
        return entries[0] if entries else None

    def remember_clone_entry(self):
        if self.model_can_clone() and self.loaded_entry() is not None:
            self.app_settings["last_clone_entry"] = self.loaded_entry_key()

    # ---------- the menu ----------

    def fill_voice_menu(self):
        menu = self.voice_menu
        menu.clear()
        self.voice_submenus = []  # PySide frees submenus without a Python reference
        active = self.active_voice_id
        voices = sorted(self.voice_library.voices, key=lambda voice: voice.name.lower())
        menu.addSection("Your voices")
        if not voices:
            menu.addAction("None yet: make one in the Studio").setEnabled(False)
        for voice in voices:
            label = voice.name
            if voice.kind == "preset":
                engine = model_registry.ENGINES.get(voice.backend)
                label += f"  ({engine.label if engine else voice.backend})"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(voice.id == active and self.voice_is_active(voice))
            action.triggered.connect(lambda _checked=False, voice=voice: self.use_voice(voice))
        menu.addSection("Built into models")
        default = menu.addAction("Chatterbox's own voice")
        default.setCheckable(True)
        default.setChecked(self.generate_voice_kind() == "default" and self.model is not None)
        default.setToolTip("Chatterbox speaks in its built-in voice when there's no clip.")
        default.triggered.connect(self.pick_default_voice)
        for entry in self.get_visible_model_entries():
            capability = model_registry.capability_for(entry)
            if capability not in BUILT_IN_TITLES:
                continue
            action = menu.addAction(BUILT_IN_TITLES[capability].format(label=entry["label"]))
            action.setCheckable(True)
            action.setChecked(self.model is not None and self.is_active_entry(entry))
            action.setToolTip("Loads the model; pick its speaker under Delivery." if capability == "preset"
                              else "Loads the model; give each speaker a voice with Cast… under Delivery.")
            action.triggered.connect(lambda _checked=False, entry=entry: self.pick_model_voice(entry))
        menu.addSeparator()
        menu.addAction("Make a new voice in the Studio…").triggered.connect(
            lambda: self.sidebar.setCurrentRow(self.PAGE_STUDIO))
        menu.addAction("Manage voices…").triggered.connect(lambda: self.sidebar.setCurrentRow(self.PAGE_VOICE))

    def pick_default_voice(self):
        entry = None
        if not (self.model is not None and self.loaded_entry() is not None
                and self.loaded_entry().get("backend") in CHATTERBOX_BACKENDS):
            entry = next((item for item in self.get_visible_model_entries()
                          if item.get("backend") in CHATTERBOX_BACKENDS), None)
            if entry is None:
                QMessageBox.information(self, "Voice", "Add a Chatterbox model on the Model page to use its voice.")
                return
        self.clear_reference_audio()
        if entry is not None:
            self.pick_model_voice(entry)
        self.refresh_model_repo_options()

    def pick_model_voice(self, entry):
        """Use the voices built into a model (presets or a conversation cast)."""
        if self.model is not None and self.is_active_entry(entry):
            return
        if self.model_busy():
            self.set_status_message("Status: Wait for the current load or generation to finish.")
            return
        self.active_voice_id = None
        self.load_entry(entry)
        self.refresh_model_repo_options()

    def load_for_clip_voice(self, voice):
        """A clip voice was picked but the loaded model can't clone: load one that can.
        Returns True when a load started (the voice is applied once it's ready)."""
        entry = self.preferred_clone_entry()
        if entry is None:
            QMessageBox.information(self, "Voice", "Add a voice cloning model on the Model page to use clip voices.")
            return True
        if self.model_busy():
            self.set_status_message("Status: Wait for the current load or generation to finish.")
            return True
        self.pending_clip_pick = voice
        self.set_status_message(f"Status: Loading {entry['label']} for {voice.name}...")
        self.load_entry(entry)
        return True

    def apply_pending_clip_pick(self):
        voice, self.pending_clip_pick = getattr(self, "pending_clip_pick", None), None
        if voice is not None and self.model_can_clone():
            path = self.voice_library.clip_path(voice)
            if path and os.path.exists(path):
                self.use_voice(voice, as_clip=True)
