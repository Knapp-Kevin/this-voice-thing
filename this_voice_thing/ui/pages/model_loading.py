"""Choosing and loading models: the model switcher, loading, releasing and voice-mode switches."""

import gc

import torch
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from this_voice_thing.core import model_registry
from this_voice_thing.ui.common import BACKEND_MULTILINGUAL
from this_voice_thing.ui.common import CHATTERBOX_AVAILABLE
from this_voice_thing.ui.common import DEFAULT_MODEL_REPO
from this_voice_thing.ui.common import DEFAULT_MULTILINGUAL_T3_MODEL
from this_voice_thing.ui.common import DUAL_MODE_BACKENDS
from this_voice_thing.ui.common import DUAL_MODE_TYPES
from this_voice_thing.ui.common import WORKER_MODEL_TYPES
from this_voice_thing.ui.threads import ModelLoaderThread


class ModelLoading:
    """Model switching and loading. Mixed into ChatterboxApp."""

    def start_default_model_load(self):
        if not CHATTERBOX_AVAILABLE:
            return
        self.set_status_message(
            "Status: Starting default model load. First run may download model files and can take several minutes. Watch Activity Log for progress."
        )
        self.load_model()

    def on_model_repo_changed(self, _index):
        entry = self.get_selected_model_entry()
        self.selected_model_repo = entry["repo_id"]
        self.refresh_model_repo_tooltip()
        self.refresh_language_options()
        if self.entry_key(entry) != self.loaded_entry_key():
            voice = next((item for item in self.voice_library.voices if item.id == self.active_voice_id), None)
            if (voice is not None and voice.has_clip and self.generate_voice_kind() == "design"
                    and model_registry.capability_for(entry) == "clone"):
                self.pending_clip_pick = voice  # a frozen designed voice moves to the cloning model
            self.load_model(entry)

    def get_selected_model_repo(self):
        return self.get_selected_model_entry()["repo_id"]

    def get_visible_model_entries(self):
        enabled_entries = [
            entry for entry in self.model_entries
            if entry.get("enabled", True)
        ]
        return enabled_entries or self.model_entries[:1]

    def refresh_model_repo_options(self):
        selected_label = None
        if hasattr(self, "model_repo_combo") and self.model_repo_combo.count() > 0:
            selected_label = self.model_repo_combo.currentText()

        visible_entries = self.get_visible_model_entries()
        # Voice first: only the models that can speak the voice in use are offered.
        fitting = [entry for entry in visible_entries if self.entry_fits_voice(entry)] or visible_entries
        self.model_repo_combo.blockSignals(True)
        self.model_repo_combo.clear()
        header_font = QFont(self.model_repo_combo.font())
        header_font.setBold(True)
        for _capability, title, members in model_registry.group_by_capability(fitting):
            self.model_repo_combo.addItem(title.upper(), None)
            header = self.model_repo_combo.model().item(self.model_repo_combo.count() - 1)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setFont(header_font)
            header.setForeground(QApplication.palette().color(QPalette.ColorRole.Link))
            for entry in members:
                self.model_repo_combo.addItem(entry["label"], visible_entries.index(entry))
                self.model_repo_combo.setItemData(
                    self.model_repo_combo.count() - 1,
                    f"{entry['repo_id']} \u00b7 {model_registry.engine_label(entry)}",
                    Qt.ItemDataRole.ToolTipRole)

        selected_index = -1
        if selected_label:
            selected_index = self.model_repo_combo.findText(selected_label)
            if selected_index >= 0 and self.model_repo_combo.itemData(selected_index) is None:
                selected_index = -1
        if selected_index < 0:
            for index, entry in enumerate(visible_entries):
                if self.entry_key(entry) == self.loaded_entry_key():
                    selected_index = self.model_repo_combo.findData(index)
                    break

        if selected_index < 0:
            for index, entry in enumerate(visible_entries):
                if entry["repo_id"] == DEFAULT_MODEL_REPO and entry.get("backend") == BACKEND_MULTILINGUAL:
                    selected_index = self.model_repo_combo.findData(index)
                    break

        if selected_index < 0 and visible_entries:
            selected_index = self.model_repo_combo.findData(0)

        if selected_index >= 0:
            self.model_repo_combo.setCurrentIndex(selected_index)
        self.model_repo_combo.blockSignals(False)

    def get_selected_model_entry(self):
        visible_entries = self.get_visible_model_entries()
        selected_index = self.model_repo_combo.currentData()
        if isinstance(selected_index, int) and 0 <= selected_index < len(visible_entries):
            return visible_entries[selected_index]
        return visible_entries[0]

    def refresh_model_repo_tooltip(self):
        selected_entry = self.get_selected_model_entry()
        tooltip = (f"{selected_entry['repo_id']} \u00b7 "
                   f"{model_registry.engine_for(selected_entry).label}\n"
                   "Switching loads the model. Manage models on the Model page.")
        if selected_entry.get("notes"):
            tooltip += f"\n\n{selected_entry['notes']}"
        self.model_repo_combo.setToolTip(tooltip)

    def set_model_loading_state(self, is_loading):
        if is_loading:
            self.model_load_progress.setEnabled(True)
            self.model_load_progress.setRange(0, 0)
        else:
            self.model_load_progress.setRange(0, 1)
            self.model_load_progress.setValue(0)
            self.model_load_progress.setEnabled(False)
        self.model_is_loading = is_loading
        self.model_repo_combo.setEnabled(not is_loading)
        self.use_preset_button.setEnabled(not is_loading)
        self.update_model_details()
        if is_loading:
            self.language_combo.setEnabled(False)
        else:
            self.refresh_language_options()

    def release_model(self):
        """Free the current model (and stop a Qwen worker) before loading another."""
        old_model, self.model = self.model, None
        if isinstance(old_model, WORKER_MODEL_TYPES):
            old_model.close()
        del old_model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(self, "qwen_row"):
            self.update_engine_controls()

    def load_model(self, selected_entry=None):
        if getattr(self, "live_voice_busy", False):
            QMessageBox.information(self, "Live Voice", "Stop Live Voice before switching models.")
            return
        if not CHATTERBOX_AVAILABLE:
            QMessageBox.critical(
                self, "Error", "ChatterboxTTS library not installed.")
            return
        if getattr(self, "model_is_loading", False):
            return
        selected_entry = selected_entry or self.get_selected_model_entry()
        selected_repo = selected_entry["repo_id"]
        selected_backend = selected_entry.get("backend", BACKEND_MULTILINGUAL)
        if (selected_backend in DUAL_MODE_BACKENDS and isinstance(self.model, DUAL_MODE_TYPES)
                and self.model.backend == selected_backend and self.model.repo_id == selected_repo):
            self.switch_voice_mode(selected_entry)
            return
        selected_multilingual_t3_model = selected_entry.get(
            "multilingual_t3_model",
            DEFAULT_MULTILINGUAL_T3_MODEL,
        )
        self.current_model_repo = selected_repo
        self.current_model_backend = selected_backend
        self.current_multilingual_t3_model = selected_multilingual_t3_model
        self.set_status_message(
            f"Status: Loading {selected_entry['label']}. A model that isn't downloaded yet "
            "is fetched first; progress appears on the Log page."
        )
        self.generate_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.release_model()
        self.set_model_loading_state(True)
        self.current_mode = model_registry.entry_mode(selected_entry)
        self.model_loader_thread = ModelLoaderThread(
            selected_repo,
            selected_backend,
            selected_multilingual_t3_model,
        )
        self.model_loader_thread.mode = self.current_mode
        self.cuda_runtime_issue = self.model_loader_thread.cuda_probe_error
        self.model_loader_thread.model_loaded.connect(self.on_model_loaded)
        self.model_loader_thread.error_occurred.connect(
            self.on_model_load_error)
        self.model_loader_thread.start()

    def switch_voice_mode(self, entry):
        """Switch a loaded dual-mode model between cloning and design without reloading."""
        self.current_mode = model_registry.entry_mode(entry)
        self.model.set_mode(self.current_mode)
        self.set_status_message(f"Status: Switched to {entry['label']} (same model, no reload). Ready.")
        self.update_engine_controls()
        self.refresh_language_options()
        self.update_text_stats()
        self.render_model_tiles()
        self.after_voice_model_ready()

    def after_voice_model_ready(self):
        if self.pending_voice is not None:
            self.apply_pending_voice()
        clip_voice = getattr(self, "pending_clip_voice", None)
        if clip_voice is not None:
            self.pending_clip_voice = None
            if self.voice_is_active(clip_voice):
                self.make_voice_clip(clip_voice)
        self.apply_pending_clip_pick()
        self.remember_clone_entry()
        self.refresh_model_repo_options()
        self.render_voice_tiles()
        self.run_pending_studio()

    def on_model_loaded(self, model_instance, device_used):
        self.model_is_warm = False
        self.model = model_instance
        self.device_used = device_used
        status_message = (
            f"Status: Model loaded from {self.current_model_repo} "
            f"using {self.current_model_backend} on {self.device_used}. Ready."
        )
        if self.system_has_nvidia_gpu and self.device_used == "cpu":
            status_message += " NVIDIA GPU detected, but PyTorch CUDA is unavailable."
        self.set_status_message(status_message)
        self.generate_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.set_model_loading_state(False)
        self.update_text_stats()
        self.refresh_models_page()
        self.update_engine_controls()
        self.after_voice_model_ready()
        if self.system_has_nvidia_gpu and self.device_used == "cpu":
            details = self.cuda_runtime_issue or (
                "This Python environment is using a CPU-only PyTorch build."
            )
            QMessageBox.warning(
                self,
                "CUDA Not Active",
                "An NVIDIA GPU was detected, but this Python environment is using "
                "a PyTorch configuration that cannot run on the detected GPU.\n\n"
                f"Details: {details}\n\n"
                "Re-run the installer to repair the PyTorch installation for CUDA.",
            )

    def on_model_load_error(self, error_msg):
        self.model = None
        self.pending_studio = None
        self.set_status_message(f"Status: Model load failed. {error_msg}")
        self.generate_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.set_model_loading_state(False)
        self.refresh_models_page()
        QMessageBox.critical(self, "Model Load Error", error_msg)
