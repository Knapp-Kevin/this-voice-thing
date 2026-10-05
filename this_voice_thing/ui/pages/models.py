"""The Model page: model tiles by capability, and adding, editing and installing models."""

import math

import torch
from PySide6.QtCore import QUrl
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtWidgets import QTabBar

from this_voice_thing.core import model_registry
from this_voice_thing.ui import tiles as model_tiles
from this_voice_thing.ui.common import BACKEND_MULTILINGUAL
from this_voice_thing.ui.common import DEFAULT_MODEL_REPO
from this_voice_thing.ui.common import DUAL_MODE_BACKENDS
from this_voice_thing.ui.common import ENGINE_INSTALL_NOTES
from this_voice_thing.ui.common import ENGINE_MODULES
from this_voice_thing.ui.common import MODEL_CONFIG_FILENAME
from this_voice_thing.ui.common import load_models_config
from this_voice_thing.ui.dialogs.models import ModelEntryDialog
from this_voice_thing.ui.threads import EngineInstallThread
from this_voice_thing.ui.widgets import dialog_accepted


class ModelPage:
    """The Model page and its model tiles. Mixed into ChatterboxApp."""

    """The Model page: model tiles, discovery, engine installs and model loading. Mixed into ChatterboxApp."""

    def _build_model_page(self):
        model_page, model_layout = self._make_page(
            "Model", "Models download once, then load from the local cache.")
        model_layout.addWidget(self._build_models_card(), 1)
        model_layout.addWidget(self._build_hf_card())
        model_layout.addWidget(self._build_tuning_card())
        self.pages.addWidget(model_page)

    def _build_models_card(self):
        models_card, models_layout = self._make_card()
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(6)
        self.capability_tabs = QTabBar()
        self.capability_tabs.setObjectName("CapabilityTabs")
        self.capability_tabs.setDrawBase(False)
        self.capability_tabs.setExpanding(False)
        self.capability_tabs.setUsesScrollButtons(False)
        self.capability_tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        for key, (title, description) in model_registry.CAPABILITIES.items():
            index = self.capability_tabs.addTab(model_registry.CAPABILITY_TABS[key])
            self.capability_tabs.setTabData(index, key)
            self.capability_tabs.setTabToolTip(index, f"{title}: {description}")
        self.capability_tabs.currentChanged.connect(lambda _index: self.render_model_tiles())
        tabs_row.addWidget(self.capability_tabs)
        tabs_row.addStretch(1)
        add_model_button = self._link(QPushButton("+ Add repo..."))
        add_model_button.setToolTip("Add a Hugging Face repo you already know, e.g. owner/model-name.")
        add_model_button.clicked.connect(self.add_model)
        tabs_row.addWidget(add_model_button)
        models_layout.addLayout(tabs_row)
        self.capability_note = QLabel()
        self.capability_note.setObjectName("Muted")
        self.capability_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        models_layout.addWidget(self.capability_note)

        your_label = QLabel("YOUR MODELS")
        your_label.setObjectName("SectionLabel")
        your_label.setToolTip("Click a model to load it. Right-click or \u22ef for edit, hide and remove.")
        models_layout.addWidget(your_label)
        self.your_tiles = model_tiles.TileArea(min_rows=1)
        models_layout.addWidget(self.your_tiles, 3)

        discover_row = QHBoxLayout()
        discover_label = QLabel("DISCOVER ON HUGGING FACE")
        discover_label.setObjectName("SectionLabel")
        discover_row.addWidget(discover_label)
        discover_row.addStretch(1)
        self.discover_input = QLineEdit()
        self.discover_input.setPlaceholderText("Search, e.g. norwegian, arabic, 0.6B")
        self.discover_input.setClearButtonEnabled(True)
        self.discover_input.setFixedWidth(210)
        self.discover_input.returnPressed.connect(self.start_discover)
        discover_row.addWidget(self.discover_input)
        discover_button = QPushButton("Search")
        discover_button.clicked.connect(self.start_discover)
        discover_row.addWidget(discover_button)
        models_layout.addLayout(discover_row)
        self.discover_tiles = model_tiles.TileArea(min_rows=1)
        models_layout.addWidget(self.discover_tiles, 2)
        self.discover_status = QLabel()
        self.discover_status.setObjectName("Muted")
        self.discover_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        models_layout.addWidget(self.discover_status)
        return models_card

    # --- Model page ---

    @staticmethod
    def entry_key(entry):
        if entry.get("backend") in DUAL_MODE_BACKENDS:
            return (entry.get("repo_id"), entry.get("backend"), model_registry.entry_mode(entry))
        return (entry.get("repo_id"), entry.get("backend"), entry.get("multilingual_t3_model") or "")

    def loaded_entry_key(self):
        backend = self.current_model_backend
        weights = self.current_multilingual_t3_model if backend == BACKEND_MULTILINGUAL else ""
        if backend in DUAL_MODE_BACKENDS:
            weights = getattr(self, "current_mode", "clone")
        return (self.current_model_repo, backend, weights or "")

    def refresh_models_page(self, select_entry=None):
        if not hasattr(self, "capability_tabs"):
            return
        self.model_cache_sizes = model_registry.cached_repo_sizes()
        if select_entry is not None:
            self.show_capability(model_registry.capability_for(select_entry))
        self.render_model_tiles()

    def show_capability(self, capability):
        for index in range(self.capability_tabs.count()):
            if self.capability_tabs.tabData(index) == capability:
                self.capability_tabs.setCurrentIndex(index)  # renders via currentChanged

    def current_capability(self):
        return self.capability_tabs.tabData(self.capability_tabs.currentIndex())

    def is_active_entry(self, entry):
        return self.model is not None and self.entry_key(entry) == self.loaded_entry_key()

    def model_busy(self):
        return (getattr(self, "model_is_loading", False) or self.is_generating or getattr(self, "api_busy", False)
                or getattr(self, "studio_busy", False))

    def render_model_tiles(self):
        if not hasattr(self, "capability_tabs"):
            return
        if getattr(self, "model_cache_sizes", None) is None:
            self.model_cache_sizes = model_registry.cached_repo_sizes()
        groups = {capability: members for capability, _title, members
                  in model_registry.group_by_capability(self.model_entries)}
        for index in range(self.capability_tabs.count()):
            capability = self.capability_tabs.tabData(index)
            title = model_registry.CAPABILITY_TABS[capability]
            count = len(groups.get(capability, []))
            self.capability_tabs.setTabText(index, f"{title}  {count}" if count else title)
        capability = self.current_capability()
        self.capability_note.setText(model_registry.CAPABILITIES[capability][1])
        self.your_tiles.set_tiles(
            [self.model_tile(entry) for entry in groups.get(capability, [])],
            "None yet. Add one from Discover below.")
        self.render_discover_tiles()

    def engine_installed(self, entry):
        module = ENGINE_MODULES.get(entry.get("backend"))
        return module is None or module.is_installed()

    def gpu_memory(self):
        """(name, GB) of the first CUDA GPU, or None."""
        if not hasattr(self, "_gpu_memory"):
            self._gpu_memory = None
            try:
                if torch.cuda.is_available():
                    props = torch.cuda.get_device_properties(0)
                    self._gpu_memory = (props.name, props.total_memory / 1024 ** 3)
            except Exception:
                pass
        return self._gpu_memory

    def hardware_line(self, backend, repo_id):
        """(kind, text, tooltip) comparing a model's GPU memory needs with this PC."""
        needs = model_registry.hardware_needs(backend, repo_id)
        wanted = f"{needs.min_gb:g} GB" if needs.good_gb == needs.min_gb else \
            f"{needs.min_gb:g}\u2013{needs.good_gb:g} GB"
        gpu = self.gpu_memory()
        detail = (f"Needs about {needs.min_gb:g} GB of GPU memory"
                  + ("" if needs.good_gb == needs.min_gb else f", {needs.good_gb:g} GB for full speed")
                  + f". {needs.note}")
        if gpu is None:
            if needs.cpu_ok:
                return "tight", "No GPU found: runs on the CPU", detail + "\nNo NVIDIA GPU was found."
            return "short", f"Needs an NVIDIA GPU ({needs.min_gb:g} GB+)", detail
        name, memory = gpu
        detail += f"\nYour GPU: {name}, {memory:.0f} GB."
        if memory + 0.5 >= needs.good_gb:
            return "good", f"\u2713 GPU {wanted} \u00b7 yours {memory:.0f} GB", detail
        if memory + 0.5 >= needs.min_gb:
            return "tight", f"! GPU {wanted} \u00b7 yours {memory:.0f} GB", \
                detail + "\nIt runs, but slower than on a bigger GPU."
        fallback = " (CPU, slow)" if needs.cpu_ok else ""
        return "short", f"\u2717 Needs {needs.min_gb:g} GB GPU{fallback} \u00b7 yours {memory:.0f} GB", detail

    def typical_speed_text(self, entry):
        """Estimated time for 1,000 characters, split the way generation would split them."""
        count = max(1, math.ceil(1000 / (self.max_section_chars_for(entry) * 0.85)))
        seconds, measured = self.estimate_seconds(entry, [1000 // count] * count)
        amount = f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.1f} min"
        return f"\u2248 {amount} per 1,000 characters", measured

    def model_tile(self, entry):
        engine = model_registry.engine_for(entry)
        active = self.is_active_entry(entry)
        repo = entry["repo_id"]
        subtitle = model_registry.engine_label(entry)
        if engine.uses_weights_version:
            subtitle += f" {model_registry.weights_file(entry).split('_')[-1].split('.')[0].upper()}"
        subtitle += f" \u00b7 {engine.languages_summary}"
        badges = []
        if active:
            badges.append(("active", "Loaded", "This model is loaded and ready to generate."))
        elif not self.engine_installed(entry):
            badges.append(("status", "Needs engine", "Click to install this engine (it runs in its own environment)."))
        elif model_registry.is_downloaded(entry):
            size = model_registry.format_size(self.model_cache_sizes.get(repo, 0))
            badges.append(("status", f"Ready \u00b7 {size}", "Downloaded; loads from the local cache."))
        elif entry.get("download_bytes"):
            size = model_registry.format_size(entry["download_bytes"])
            badges.append(("status", f"Download {size}", "Downloads the first time you load it."))
        else:
            badges.append(("status", "Not downloaded", "Downloads the first time you load it."))
        badges.append(model_tiles.license_badge(model_registry.license_of(entry)))
        speed, measured = self.typical_speed_text(entry)
        if not entry.get("enabled", True):
            speed = f"Hidden · {speed}"
        tooltip = "\n".join(line for line in (
            f"{entry['label']}  ({repo})",
            engine.description,
            entry.get("notes", ""),
            "" if entry.get("enabled", True) else "Hidden from the model switcher on the Generate page.",
            f"Speed {'measured from your runs' if measured else 'estimated until you generate with it'}.",
            "" if active else "Click to load. Right-click for more.") if line)
        needs = self.hardware_line(entry.get("backend"), repo)
        tooltip += "\n" + needs[2]
        tile = model_tiles.ModelTile(entry["label"], subtitle, badges, speed, tooltip,
                                     active=active, with_menu=True, needs=needs)
        tile.clicked.connect(lambda e=entry: self.load_entry(e))
        tile.menu_requested.connect(lambda pos, e=entry: self.show_model_menu(e, pos))
        return tile

    def show_model_menu(self, entry, pos):
        menu = QMenu(self)
        active = self.is_active_entry(entry)
        hidden = not entry.get("enabled", True)
        is_default = entry["repo_id"] == DEFAULT_MODEL_REPO and entry.get("backend") == BACKEND_MULTILINGUAL \
            and sum(1 for e in self.model_entries if self.entry_key(e) == self.entry_key(entry)) == 1
        visible = sum(1 for e in self.model_entries if e.get("enabled", True))
        actions = (
            ("Loaded" if active else "Load", lambda: self.load_entry(entry),
             not active and not self.model_busy()),
            None,
            ("Edit...", lambda: self.edit_model(entry), True),
            ("Duplicate...", lambda: self.duplicate_model(entry), True),
            ("Show in model switcher" if hidden else "Hide from model switcher",
             lambda: self.toggle_model_hidden(entry), hidden or visible > 1),
            ("Open on Hugging Face",
             lambda: QDesktopServices.openUrl(QUrl(f"https://huggingface.co/{entry['repo_id']}")), True),
            None,
            ("Remove (official fallback)" if is_default else "Remove (unload it first)" if active
             else "Remove...", lambda: self.remove_model(entry), not is_default and not active),
        )
        for item in actions:
            if item is None:
                menu.addSeparator()
                continue
            text, callback, enabled = item
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(lambda _checked=False, callback=callback: callback())
        menu.exec(pos)

    def update_model_details(self):
        self.render_model_tiles()

    def persist_model_entries(self, entries, select_entry=None):
        try:
            model_registry.save_models_config(self.model_config_path, entries)
        except OSError as exc:
            QMessageBox.warning(self, "Could Not Save", f"{MODEL_CONFIG_FILENAME} could not be written:\n{exc}")
            return False
        self.model_entries = load_models_config(self.model_config_path)
        self.refresh_model_repo_options()
        self.refresh_model_repo_tooltip()
        self.refresh_models_page(select_entry)
        self.set_status_message(f"Status: Saved model list to {MODEL_CONFIG_FILENAME}.")
        return True

    def _edit_entry_dialog(self, entry, replacing=None):
        others = [e for e in self.model_entries if e is not replacing]
        dialog = ModelEntryDialog(entry, others, self.app_settings.get("hf_token"), self)
        if not dialog_accepted(dialog.exec()):
            return None
        return dialog.result_entry()

    def add_model(self):
        new_entry = self._edit_entry_dialog(None)
        if new_entry:
            self.persist_model_entries(self.model_entries + [new_entry], new_entry)

    def edit_model(self, entry):
        updated = self._edit_entry_dialog(entry, replacing=entry)
        if updated:
            entries = [updated if e is entry else e for e in self.model_entries]
            self.persist_model_entries(entries, updated)

    def duplicate_model(self, entry):
        copy = dict(entry)
        base, n = f"{entry['label']} copy", 2
        copy["label"] = base
        while any(e.get("label") == copy["label"] for e in self.model_entries):
            copy["label"] = f"{base} {n}"
            n += 1
        updated = self._edit_entry_dialog(copy)
        if updated:
            self.persist_model_entries(self.model_entries + [updated], updated)

    def toggle_model_hidden(self, entry):
        updated = dict(entry, enabled=not entry.get("enabled", True))
        self.persist_model_entries([updated if e is entry else e for e in self.model_entries], updated)

    def remove_model(self, entry):
        answer = QMessageBox.question(
            self, "Remove Model",
            f"Remove '{entry['label']}' from the model list?\n\nDownloaded files stay in the "
            "Hugging Face cache, so adding it back later won't download again.")
        if answer == QMessageBox.StandardButton.Yes:
            self.persist_model_entries([e for e in self.model_entries if e is not entry])

    def load_entry(self, entry):
        if self.is_active_entry(entry):
            return
        if self.model_busy():
            self.set_status_message("Status: Wait for the current load or generation to finish.")
            return
        if not self.engine_installed(entry):
            self.install_engine(entry)
            return
        index = self.model_repo_combo.findText(entry["label"])
        if index >= 0 and index != self.model_repo_combo.currentIndex():
            self.model_repo_combo.setCurrentIndex(index)  # loads via the switcher
        elif self.entry_key(entry) != self.loaded_entry_key() or self.model is None:
            self.load_model(entry)

    def install_engine(self, entry):
        backend = entry.get("backend")
        name, note = ENGINE_INSTALL_NOTES[backend]
        if getattr(self, "engine_install_thread", None) is not None and self.engine_install_thread.isRunning():
            self.set_status_message("Status: An engine is still installing. Progress is on the Log page.")
            return
        answer = QMessageBox.question(self, f"Install {name} Engine", f"{note}\n\nInstall now?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_status_message(f"Status: Installing the {name} engine. Progress is on the Log page.")
        self.pending_install_entry = entry
        self.engine_install_thread = EngineInstallThread(ENGINE_MODULES[backend])
        self.engine_install_thread.finished_with.connect(
            lambda error, name=name: self.on_engine_install_finished(name, error))
        self.engine_install_thread.start()

    def on_engine_install_finished(self, name, error):
        if error:
            self.set_status_message(f"Status: {name} engine install failed. See the Log page.")
            QMessageBox.warning(self, f"{name} Engine", f"The install failed:\n{error}")
        else:
            self.set_status_message(f"Status: {name} engine installed. Loading the model...")
            self.refresh_models_page()
            if self.pending_install_entry is not None:
                self.load_entry(self.pending_install_entry)
        self.render_model_tiles()
