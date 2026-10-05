"""Finishing touches: the panel under Delivery and its settings."""

from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QWidget

from this_voice_thing.core import audio_effects
from this_voice_thing.ui.common import LOSSLESS_FORMATS
from this_voice_thing.ui.widgets import ElidedLabel


class Finishing:
    """Finishing touches applied after generation. Mixed into ChatterboxApp."""

    def _build_finishing_panel(self, delivery_layout):
        finishing_header = QHBoxLayout()
        self.finishing_toggle = self._link(QPushButton())
        self.finishing_toggle.setToolTip("Adjustments applied to the audio after it is generated.")
        self.finishing_toggle.clicked.connect(
            lambda: self.set_finishing_expanded(self.finishing_panel.isHidden()))
        finishing_header.addWidget(self.finishing_toggle)
        # Elided to fit: with several effects on, the summary would otherwise widen the window.
        self.finishing_summary_label = ElidedLabel()
        self.finishing_summary_label.setObjectName("Muted")
        finishing_header.addWidget(self.finishing_summary_label, 1)
        delivery_layout.addLayout(finishing_header)

        self.finishing_panel = QWidget()
        finishing_grid = QGridLayout(self.finishing_panel)
        finishing_grid.setContentsMargins(0, 0, 0, 0)
        finishing_grid.setHorizontalSpacing(14)
        finishing_grid.setColumnStretch(1, 1)
        finishing_grid.setColumnStretch(3, 1)

        def add_finishing(row, column, title, widget, tooltip):
            label = QLabel(title)
            label.setToolTip(tooltip)
            widget.setToolTip(tooltip)
            finishing_grid.addWidget(label, row, column)
            finishing_grid.addWidget(widget, row, column + 1)

        self.pause_slider = self._create_slider(
            *audio_effects.PAUSE_RANGE, 0.1, 0.6, "{:.1f} s")
        add_finishing(0, 0, "Paragraph pause", self.pause_slider,
                      "Silence between paragraphs (headings get a little more). Pauses between "
                      "sentences and inside long sentences are kept short and even automatically.")
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(LOSSLESS_FORMATS)
        add_finishing(0, 2, "Save as", self.output_format_combo,
                      "WAV is uncompressed; FLAC is lossless and about half the size. "
                      "MP3 is on the Advanced page.")
        finishing_checks = QHBoxLayout()
        self.even_volume_checkbox = QCheckBox("Even out volume")
        self.even_volume_checkbox.setToolTip(
            "Bring every result to a consistent, comfortable loudness without clipping.")
        self.trim_silence_checkbox = QCheckBox("Trim silence")
        self.trim_silence_checkbox.setToolTip(
            "Remove dead air before the first word and after the last.")
        finishing_checks.setSpacing(18)
        self.subtitles_checkbox = QCheckBox("Save subtitles")
        self.subtitles_checkbox.setToolTip(
            "Also save captions timed to the audio, next to it (.srt, or .vtt: the format is on the "
            "Advanced page). Timing comes from the generated sections and the pauses in them.")
        finishing_checks.addWidget(self.even_volume_checkbox)
        finishing_checks.addWidget(self.trim_silence_checkbox)
        finishing_checks.addWidget(self.subtitles_checkbox)
        finishing_checks.addStretch(1)
        reset_finishing_button = QPushButton("Reset")
        reset_finishing_button.setToolTip(
            "Restore the default finishing settings (Advanced effects are kept).")
        reset_finishing_button.clicked.connect(self.reset_finishing)
        finishing_checks.addWidget(reset_finishing_button)
        finishing_grid.addLayout(finishing_checks, 1, 0, 1, 4)
        delivery_layout.addWidget(self.finishing_panel)
        self.finishing_toggle.setToolTip(
            "Adjustments applied to the audio after it is generated. Speed, pitch and MP3 "
            "are on the Advanced page.")

        self.pause_slider.slider.valueChanged.connect(self.update_finishing_summary)
        self.output_format_combo.currentTextChanged.connect(self.update_finishing_summary)
        self.even_volume_checkbox.toggled.connect(self.update_finishing_summary)
        self.trim_silence_checkbox.toggled.connect(self.update_finishing_summary)
        self.subtitles_checkbox.toggled.connect(self.update_finishing_summary)

    # --- Finishing touches ---

    def current_finishing_settings(self):
        return audio_effects.FinishingSettings(
            speed=round(self.speed_slider.get_value(), 2),
            pitch_semitones=round(self.pitch_slider.get_value(), 1),
            paragraph_pause=round(self.pause_slider.get_value(), 1),
            even_volume=self.even_volume_checkbox.isChecked(),
            trim_silence=self.trim_silence_checkbox.isChecked(),
            output_format="MP3" if self.mp3_checkbox.isChecked() else self.output_format_combo.currentText(),
            save_subtitles=self.subtitles_checkbox.isChecked(),
            subtitle_format=self.subtitle_format_combo.currentText(),
        )

    def apply_finishing_settings(self, settings):
        self.speed_slider.set_value(settings.speed)
        self.pitch_slider.set_value(settings.pitch_semitones)
        self.pause_slider.set_value(settings.paragraph_pause)
        self.even_volume_checkbox.setChecked(settings.even_volume)
        self.trim_silence_checkbox.setChecked(settings.trim_silence)
        self.mp3_checkbox.setChecked(settings.output_format == "MP3")
        if settings.output_format in LOSSLESS_FORMATS:
            self.output_format_combo.setCurrentText(settings.output_format)
        self.subtitles_checkbox.setChecked(settings.save_subtitles)
        self.subtitle_format_combo.setCurrentText(settings.subtitle_format)
        self.update_finishing_summary()

    def reset_finishing(self):
        """Finishing touches only; Advanced effects are left as they are."""
        defaults = audio_effects.FinishingSettings()
        self.pause_slider.set_value(defaults.paragraph_pause)
        self.even_volume_checkbox.setChecked(defaults.even_volume)
        self.trim_silence_checkbox.setChecked(defaults.trim_silence)
        self.output_format_combo.setCurrentText(defaults.output_format)
        self.subtitles_checkbox.setChecked(defaults.save_subtitles)
        self.update_finishing_summary()

    def on_mp3_toggled(self, checked):
        self.output_format_combo.setEnabled(not checked)
        self.output_format_combo.setToolTip(
            "MP3 is selected on the Advanced page." if checked else
            "WAV is uncompressed; FLAC is lossless and about half the size. "
            "MP3 is on the Advanced page.")
        self.update_finishing_summary()

    def update_finishing_summary(self, *_args):
        if not hasattr(self, "subtitle_format_combo"):
            return  # Advanced page not built yet
        settings = self.current_finishing_settings()
        summary = settings.summary()
        advanced = (abs(settings.speed - 1.0) > 1e-6 or abs(settings.pitch_semitones) > 1e-6
                    or settings.output_format == "MP3")
        self.finishing_summary_label.setText(summary + ("  (effects on Advanced page)" if advanced else ""))

    def set_finishing_expanded(self, expanded):
        self.finishing_panel.setVisible(expanded)
        arrow = "\u25be" if expanded else "\u25b8"
        self.finishing_toggle.setText(f"{arrow} Finishing touches")
        self.finishing_summary_label.setVisible(not expanded)
        if self.isVisible():
            self.update_minimum_size()
