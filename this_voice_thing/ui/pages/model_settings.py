"""Model page settings: the Hugging Face token and sampling tuning cards."""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QDoubleSpinBox
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtWidgets import QWidget

from this_voice_thing.core import model_registry
from this_voice_thing.ui import theme as ui_theme


# Hugging Face's "new access token" page with the Read type preselected (all the app
# needs); signed-out users sign in first and land back here.
HF_NEW_TOKEN_URL = "https://huggingface.co/settings/tokens/new?tokenType=read"


class ModelSettings:
    """Hugging Face access and sampling tuning. Mixed into ChatterboxApp."""

    def _build_hf_card(self):
        hf_card, hf_layout = self._make_card("Hugging Face access")
        hf_row = QHBoxLayout()
        self.hf_token_input = QLineEdit(str(self.app_settings.get("hf_token", "")))
        self.hf_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token_input.setPlaceholderText("hf_... (optional)")
        self.hf_token_input.returnPressed.connect(self.save_hf_token)
        hf_row.addWidget(self.hf_token_input, 1)
        save_token_button = QPushButton("Save")
        save_token_button.clicked.connect(self.save_hf_token)
        hf_row.addWidget(save_token_button)
        test_token_button = QPushButton("Test")
        test_token_button.clicked.connect(self.test_hf_token)
        hf_row.addWidget(test_token_button)
        get_token_button = self._link(QPushButton("Get a token"))
        get_token_button.setToolTip(
            "Opens Hugging Face in your browser to create a free Read token (sign in or sign up "
            "first). Copy it, paste it here, then Save.")
        get_token_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(HF_NEW_TOKEN_URL)))
        hf_row.addWidget(get_token_button)
        hf_layout.addLayout(hf_row)
        self.hf_token_status = QLabel()
        self.hf_token_status.setObjectName("Muted")
        self.hf_token_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hf_layout.addWidget(self.hf_token_status)
        return hf_card

    def _build_tuning_card(self):
        tuning_card, tuning_layout = self._make_card()
        tuning_header = QHBoxLayout()
        self.tuning_toggle = self._link(QPushButton())
        self.tuning_toggle.clicked.connect(lambda: self.set_tuning_expanded(self.tuning_panel.isHidden()))
        tuning_header.addWidget(self.tuning_toggle)
        self.tuning_summary_label = QLabel()
        self.tuning_summary_label.setObjectName("Muted")
        tuning_header.addWidget(self.tuning_summary_label)
        tuning_header.addStretch(1)
        tuning_layout.addLayout(tuning_header)
        self.tuning_panel = QWidget()
        tuning_grid = QGridLayout(self.tuning_panel)
        tuning_grid.setContentsMargins(0, 0, 0, 0)
        tuning_grid.setHorizontalSpacing(14)

        def tuning_spin(minimum, maximum, step, value):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            spin.valueChanged.connect(self.on_tuning_changed)
            return spin

        self.repetition_spin = tuning_spin(0.5, 3.0, 0.05, self.repetition_penalty)
        self.min_p_spin = tuning_spin(0.0, 1.0, 0.01, self.min_p)
        self.top_p_spin = tuning_spin(0.0, 1.0, 0.01, self.top_p)
        for column, (title, spin, tip) in enumerate((
                ("Repetition control", self.repetition_spin,
                 "Discourages repeated words and stutters. Default 1.20; raise slightly if "
                 "phrases repeat. [repetition_penalty]"),
                ("Unlikely-sound filter", self.min_p_spin,
                 "Skips very unlikely sounds. Default 0.05; higher is steadier but flatter. [min_p]"),
                ("Top-p", self.top_p_spin,
                 "Limits choices to the most likely sounds. 1.00 means off. [top_p]"))):
            caption = QLabel(title)
            caption.setToolTip(tip)
            spin.setToolTip(tip)
            row, col = divmod(column, 2)
            tuning_grid.addWidget(caption, row, col * 2)
            tuning_grid.addWidget(spin, row, col * 2 + 1)
        tuning_grid.setColumnStretch(1, 1)
        tuning_grid.setColumnStretch(3, 1)
        tuning_reset = QPushButton("Reset")
        tuning_reset.clicked.connect(self.reset_tuning)
        tuning_grid.addWidget(tuning_reset, 1, 3, Qt.AlignmentFlag.AlignRight)
        tuning_layout.addWidget(self.tuning_panel)
        return tuning_card

    def apply_hf_token_setting(self):
        hf_token = str(self.app_settings.get("hf_token", "")).strip()
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token
        else:
            os.environ.pop("HF_TOKEN", None)

    def save_hf_token(self):
        token = self.hf_token_input.text().strip()
        if token:
            self.app_settings["hf_token"] = token
        else:
            self.app_settings.pop("hf_token", None)
        self.apply_hf_token_setting()
        self.save_app_settings()
        self.update_hf_token_status("Token saved." if token else "Token removed.")

    def test_hf_token(self):
        token = self.hf_token_input.text().strip()
        if not token:
            self.update_hf_token_status("Enter a token to test.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, message = model_registry.whoami(token)
        finally:
            QApplication.restoreOverrideCursor()
        self.update_hf_token_status(message, ok)

    def update_hf_token_status(self, message=None, ok=None):
        saved = bool(self.app_settings.get("hf_token"))
        base = ("A token is saved and used for downloads." if saved else
                "Optional: only needed for gated or private repos. Get a token creates a free one.")
        self.hf_token_status.setText(f"{message}  {base}" if message else base)
        ui_theme.set_tone(self.hf_token_status, "success" if ok else "error" if ok is False else "")
        self.hf_token_status.setToolTip(
            "Public models need no token. A token unlocks gated or private repos and higher "
            "download limits. It is stored only in app_settings.json on this computer (ignored "
            "by git), never in models.json.")

    def on_tuning_changed(self, *_args):
        self.repetition_penalty = self.repetition_spin.value()
        self.min_p = self.min_p_spin.value()
        self.top_p = self.top_p_spin.value()
        self.app_settings["sampling"] = {
            "repetition_penalty": self.repetition_penalty, "min_p": self.min_p, "top_p": self.top_p}
        defaults = (abs(self.repetition_penalty - 1.2) < 1e-9 and abs(self.min_p - 0.05) < 1e-9
                    and abs(self.top_p - 1.0) < 1e-9)
        self.tuning_summary_label.setText("defaults" if defaults else
                                          f"repetition {self.repetition_penalty:.2f}, "
                                          f"min-p {self.min_p:.2f}, top-p {self.top_p:.2f}")

    def reset_tuning(self):
        self.repetition_spin.setValue(1.2)
        self.min_p_spin.setValue(0.05)
        self.top_p_spin.setValue(1.0)

    def set_tuning_expanded(self, expanded):
        self.tuning_panel.setVisible(expanded)
        arrow = "\u25be" if expanded else "\u25b8"
        self.tuning_toggle.setText(f"{arrow} Fine-tuning")
        self.app_settings["tuning_expanded"] = expanded
        self.on_tuning_changed()
        if self.isVisible():
            self.update_minimum_size()
