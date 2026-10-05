"""The voice library card on the Voices page: tiles, their menu, previewing, editing and removing voices."""

import os

from PySide6.QtCore import QUrl
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtWidgets import QTabBar

from this_voice_thing.core import model_registry
from this_voice_thing.core import voice_library
from this_voice_thing.engines import kokoro as kokoro_engine
from this_voice_thing.ui import tiles as model_tiles
from this_voice_thing.ui.common import KOKORO_BACKEND
from this_voice_thing.ui.common import MAX_RECORDING_SECONDS
from this_voice_thing.ui.common import MIN_RECORDING_SECONDS
from this_voice_thing.ui.dialogs.voices import VoiceDetailsDialog
from this_voice_thing.ui.widgets import dialog_accepted


class Library:
    """The voice library. Mixed into ChatterboxApp."""

    """The voice library. Mixed into ChatterboxApp."""

    def _build_library_card(self):
        library_card, library_layout = self._make_card()
        library_header = QHBoxLayout()
        self.voice_filter_tabs = QTabBar()
        self.voice_filter_tabs.setObjectName("CapabilityTabs")
        self.voice_filter_tabs.setDrawBase(False)
        self.voice_filter_tabs.setExpanding(False)
        self.voice_filter_tabs.setUsesScrollButtons(False)
        self.voice_filter_tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        for key, title in (("", "All"), ("clip", "Clips"), ("preset", "Presets"), ("design", "Designed")):
            self.voice_filter_tabs.setTabData(self.voice_filter_tabs.addTab(title), key)
        self.voice_filter_tabs.currentChanged.connect(lambda _index: self.render_voice_tiles())
        library_header.addWidget(self.voice_filter_tabs)
        library_header.addStretch(1)
        self.voice_search = QLineEdit()
        self.voice_search.setPlaceholderText("Search voices")
        self.voice_search.setToolTip("Matches names, tags and notes.")
        self.voice_search.setClearButtonEnabled(True)
        self.voice_search.setFixedWidth(170)
        self.voice_search.textChanged.connect(lambda _text: self.render_voice_tiles())
        library_header.addWidget(self.voice_search)
        library_layout.addLayout(library_header)
        library_hint = QLabel("Click a voice to use it on Generate. New voices start in the Studio: "
                              "record, add a file, or design one.")
        library_hint.setObjectName("Muted")
        library_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        library_layout.addWidget(library_hint)
        self.voice_tiles = model_tiles.TileArea(min_rows=1)
        library_layout.addWidget(self.voice_tiles, 1)
        library_actions = QHBoxLayout()
        self.mic_combo = QComboBox()
        self.mic_combo.setToolTip("Microphone used for Record...")
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mic_combo.setMinimumContentsLength(10)
        library_actions.addWidget(self.mic_combo, 1)
        self.record_button = self._accent(QPushButton("Record..."))
        self.record_button.setToolTip(
            "Record a new clip voice: read about 15 seconds in a quiet room; the first few seconds "
            f"matter most ({MIN_RECORDING_SECONDS}-{MAX_RECORDING_SECONDS} s).")
        self.record_button.clicked.connect(lambda: self.open_recording_dialog(then=self.studio_set_source))
        library_actions.addWidget(self.record_button)
        self.media_devices.audioInputsChanged.connect(self.populate_microphones)
        self.populate_microphones()
        browse_ref_button = QPushButton("Add a file...")
        browse_ref_button.setToolTip("Open a .wav, .mp3 or .flac clip in the Studio to clean it up and save it.")
        browse_ref_button.clicked.connect(self.studio_open_source_file)
        library_actions.addWidget(browse_ref_button)
        open_recordings_button = self._link(QPushButton("Open folder"))
        open_recordings_button.clicked.connect(self.open_recordings_folder)
        library_actions.addWidget(open_recordings_button)
        library_layout.addLayout(library_actions)
        return library_card

    # --- Voice library ---

    def voice_is_active(self, voice):
        if voice.kind == "clip":
            path = self.voice_library.clip_path(voice)
            return bool(path) and os.path.normcase(os.path.abspath(path)) == os.path.normcase(
                os.path.abspath(self.reference_path or "~none~"))
        model = self.active_qwen_model()
        return (voice.id == self.active_voice_id and model is not None
                and getattr(model, "backend", "") == voice.backend)

    def render_voice_tiles(self):
        if not hasattr(self, "voice_tiles"):
            return
        kind = self.voice_filter_tabs.tabData(self.voice_filter_tabs.currentIndex())
        query = self.voice_search.text().strip().lower()
        voices = [voice for voice in self.voice_library.voices
                  if (not kind or voice.kind == kind or (kind == "clip" and voice.has_clip))
                  and (not query or query in " ".join([voice.name, voice.notes, *voice.tags]).lower())]
        voices.sort(key=lambda voice: voice.created, reverse=True)
        counts = {key: sum(1 for voice in self.voice_library.voices
                           if not key or voice.kind == key or (key == "clip" and voice.has_clip))
                  for key in ("", "clip", "preset", "design")}
        for index in range(self.voice_filter_tabs.count()):
            key = self.voice_filter_tabs.tabData(index)
            title = {"": "All", "clip": "Clips", "preset": "Presets", "design": "Designed"}[key]
            self.voice_filter_tabs.setTabText(index, f"{title}  {counts[key]}" if counts[key] else title)
        empty = ("No voices match." if query or kind else
                 "No voices yet. Record one, add a file, or save the voice you're using.")
        self.voice_tiles.set_tiles([self.voice_tile(voice) for voice in voices], empty)

    def voice_tile(self, voice):
        library = self.voice_library
        active = self.voice_is_active(voice)
        engine = model_registry.ENGINES.get(voice.backend)
        path = library.clip_path(voice)
        if voice.kind == "clip":
            exists = os.path.exists(path)
            seconds = voice_library.clip_seconds(path) if exists else 0
            subtitle = f"Clip \u00b7 {seconds:.0f} s" if exists else "Clip \u00b7 file missing"
            transcript = voice_library.read_transcript(path) if exists else ""
            detail = f"\u201c{transcript}\u201d" if transcript else "No transcript"
        elif voice.kind == "preset":
            label = kokoro_engine.voice_label(voice.speaker) if voice.backend == KOKORO_BACKEND \
                else voice.speaker.replace("_", " ").title()
            subtitle = f"Preset \u00b7 {engine.label if engine else voice.backend} \u00b7 {label}"
            detail = voice.style or voice.notes or "Built-in voice"
        else:
            subtitle = f"Designed \u00b7 {engine.label if engine else voice.backend}"
            detail = voice.description
        badges = [("active", "In use", "This is the voice you're using.")] if active else []
        if voice.kind != "clip" and voice.has_clip:
            badges.append(("status", "Has clip", "Also usable as a clip voice by cloning models."))
        badges += [("status", tag, "Tag") for tag in voice.tags[:2]]
        tooltip = "\n".join(line for line in (voice.name, subtitle, detail if voice.kind != "clip" else "",
                                               voice.notes, path, "Click to use. Right-click for more.") if line)
        tile = model_tiles.ModelTile(voice.name, subtitle, badges, detail, tooltip, active=active, with_menu=True)
        tile.clicked.connect(lambda v=voice: self.use_voice(v))
        tile.menu_requested.connect(lambda pos, v=voice: self.show_voice_menu(v, pos))
        return tile

    def show_voice_menu(self, voice, pos):
        menu = QMenu(self)
        path = self.voice_library.clip_path(voice)
        playing = self.preview_button_playing is self.voice_tiles and getattr(self, "previewing_voice", None) is voice
        entries = [
            ("Use", lambda: self.use_voice(voice), True),
            ("Stop preview" if playing else "Preview clip", lambda: self.preview_voice(voice),
             bool(path) and os.path.exists(path)),
        ]
        if path and os.path.exists(path):
            has_text = bool(voice_library.read_transcript(path))
            entries.append(("Transcribe again" if has_text else "Transcribe clip",
                            lambda: self.transcribe_voice_clip(voice), True))
        if voice.kind != "clip":
            entries.append(("Use as a clip voice", lambda: self.use_voice(voice, as_clip=True),
                            bool(path) and os.path.exists(path)))
            entries.append(("Make clip..." if not voice.has_clip else "Remake clip...",
                            lambda: self.make_voice_clip(voice), not self.model_busy()))
        if path and os.path.exists(path):
            entries.append(("Open in Studio", lambda: self.open_voice_in_studio(voice), True))
        entries += [None, ("Edit...", lambda: self.edit_voice(voice), True),
                    ("Show file in folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))),
                     bool(path) and os.path.exists(path)),
                    None, ("Remove from library...", lambda: self.remove_voice(voice), True)]
        for item in entries:
            if item is None:
                menu.addSeparator()
                continue
            text, callback, enabled = item
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(lambda _checked=False, callback=callback: callback())
        menu.exec(pos)

    def preview_voice(self, voice):
        if self.preview_button_playing is self.voice_tiles and getattr(self, "previewing_voice", None) is voice:
            self.stop_reference_preview()
            return
        self.stop_reference_preview()
        self.previewing_voice = voice
        self.preview_button_playing = self.voice_tiles
        self.preview_player.setSource(QUrl.fromLocalFile(self.voice_library.clip_path(voice)))
        self.preview_player.play()

    def edit_voice(self, voice):
        path = self.voice_library.clip_path(voice)
        transcript = voice_library.read_transcript(path) if path and os.path.exists(path) else None
        dialog = VoiceDetailsDialog("Edit voice", voice, self.voice_library, transcript, parent=self)
        if not dialog_accepted(dialog.exec()):
            return
        transcript = dialog.apply()
        if transcript is not None:
            voice_library.write_transcript(path, transcript)
            if self.voice_is_active(voice):
                self.load_reference_transcript(path)
        self.voice_library.save()
        self.render_voice_tiles()

    def remove_voice(self, voice):
        path = self.voice_library.clip_path(voice)
        owned = bool(path) and os.path.abspath(path).startswith(os.path.abspath(self.voice_library.clips_dir))
        detail = ("Its clip, made by the library, is deleted too." if owned else
                  "The audio file stays where it is." if path else "")
        answer = QMessageBox.question(self, "Remove Voice", f"Remove {voice.name} from the library?\n\n{detail}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.preview_button_playing is self.voice_tiles:
            self.stop_reference_preview()
        if owned and self.voice_is_active(voice):
            self.set_reference_audio(None)
        self.voice_library.remove(voice)
        self.render_voice_tiles()
