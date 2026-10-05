"""The Advanced page: voice effects and export settings."""

from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QPushButton

from this_voice_thing.core import audio_effects
from this_voice_thing.core import subtitles
from this_voice_thing.ui import theme as ui_theme


class AdvancedPage:
    """The Advanced page. Mixed into ChatterboxApp."""

    """The Advanced page: local API and pronunciation dictionary. Mixed into ChatterboxApp."""

    def _build_advanced_page(self):
        advanced_page, advanced_layout = self._make_page(
            "Advanced", "Extra processing applied to results after they are generated.")
        advanced_layout.addWidget(self._build_effects_card())
        advanced_layout.addWidget(self._build_pronunciation_card())
        self.update_pronunciation_summary()
        advanced_layout.addWidget(self._build_api_card())
        advanced_layout.addWidget(self._build_export_card())

        advanced_actions = QHBoxLayout()
        advanced_actions.setContentsMargins(ui_theme.SHADOW, 0, ui_theme.SHADOW, 0)
        advanced_actions.addStretch(1)
        reset_advanced_button = QPushButton("Reset effects")
        reset_advanced_button.setToolTip("Speed 1.00x, pitch 0, lossless output.")
        reset_advanced_button.clicked.connect(self.reset_advanced)
        advanced_actions.addWidget(reset_advanced_button)
        advanced_layout.addLayout(advanced_actions)
        advanced_layout.addStretch(1)
        self.pages.addWidget(advanced_page)

        for slider in (self.speed_slider, self.pitch_slider):
            slider.slider.valueChanged.connect(self.update_finishing_summary)
        self.mp3_checkbox.toggled.connect(self.on_mp3_toggled)
        self.apply_finishing_settings(
            audio_effects.FinishingSettings.from_dict(self.app_settings.get("finishing")))
        self.set_finishing_expanded(bool(self.app_settings.get("finishing_expanded", False)))

    def _build_effects_card(self):
        effects_card, effects_layout = self._make_card("Voice effects")
        watermark_note = QLabel(
            "Pitch, speed and MP3 compression change the audio after it is generated and can "
            "weaken the inaudible watermark that marks it as AI-generated. Leave them at their "
            "defaults if that matters to you.")
        watermark_note.setObjectName("Note")
        watermark_note.setWordWrap(True)
        effects_layout.addWidget(watermark_note)
        effects_grid = QGridLayout()
        effects_grid.setHorizontalSpacing(14)
        effects_grid.setColumnStretch(1, 1)
        effects_grid.setColumnStretch(3, 1)
        self.speed_slider = self._create_slider(
            *audio_effects.SPEED_RANGE, 0.05, 1.0, "{:.2f}x")
        self.pitch_slider = self._create_slider(
            *audio_effects.PITCH_RANGE, 0.5, 0.0, "{:+.1f} st")
        for column, (title, slider, tip) in enumerate((
                ("Speed", self.speed_slider,
                 "Speaking speed without changing the pitch. 1.00x is unchanged."),
                ("Pitch", self.pitch_slider,
                 "Raise or lower the voice in semitones while keeping its natural character. "
                 "Small changes (1-2 st) sound most natural."))):
            caption = QLabel(title)
            caption.setToolTip(tip)
            slider.setToolTip(tip)
            effects_grid.addWidget(caption, 0, column * 2)
            effects_grid.addWidget(slider, 0, column * 2 + 1)
        effects_layout.addLayout(effects_grid)
        return effects_card

    def _build_export_card(self):
        export_card, export_layout = self._make_card("Export")
        self.mp3_checkbox = QCheckBox("Save results as MP3")
        self.mp3_checkbox.setToolTip(
            "Smallest files and plays everywhere, but lossy. Replaces the WAV/FLAC choice in "
            "Finishing touches while ticked.")
        export_layout.addWidget(self.mp3_checkbox)
        mp3_hint = QLabel("Lossy compression; replaces the WAV/FLAC choice on the Generate page.")
        mp3_hint.setObjectName("Muted")
        export_layout.addWidget(mp3_hint)
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(QLabel("Subtitle format"))
        self.subtitle_format_combo = QComboBox()
        self.subtitle_format_combo.addItems(list(subtitles.FORMATS))
        self.subtitle_format_combo.setToolTip(
            "SRT works almost everywhere (video editors, YouTube, VLC). WebVTT is for web players; "
            "in conversations it tags each caption with the speaker.")
        self.subtitle_format_combo.currentTextChanged.connect(self.update_finishing_summary)
        subtitle_row.addWidget(self.subtitle_format_combo)
        subtitle_row.addStretch(1)
        export_layout.addLayout(subtitle_row)
        subtitle_hint = QLabel("Used when Save subtitles is ticked in Finishing touches.")
        subtitle_hint.setObjectName("Muted")
        export_layout.addWidget(subtitle_hint)
        return export_card

    def reset_advanced(self):
        self.speed_slider.set_value(1.0)
        self.pitch_slider.set_value(0.0)
        self.mp3_checkbox.setChecked(False)
        self.update_finishing_summary()
