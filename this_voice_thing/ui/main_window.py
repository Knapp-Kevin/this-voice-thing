"""The main window: sidebar, pages and app settings. Run as __main__ to start the app."""

import base64
import os
import sys

import torch
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from this_voice_thing import paths
from this_voice_thing.core import pronunciation, transcription, voice_library
from this_voice_thing.integrations import google_docs, local_api
from this_voice_thing.ui import common, taskbar
from this_voice_thing.ui import theme as ui_theme
from this_voice_thing.ui.api_bridge import ApiBridge
from this_voice_thing.ui.common import (
    APP_SETTINGS_FILENAME,
    BACKEND_MULTILINGUAL,
    CHATTERBOX_AVAILABLE,
    DEFAULT_MODEL_REPO,
    DEFAULT_MULTILINGUAL_T3_MODEL,
    has_system_nvidia_gpu,
    load_models_config,
    MODEL_CONFIG_FILENAME,
    read_json_payload,
    REFERENCE_RECORDINGS_DIRNAME,
    resolve_multilingual_t3_model,
    WORKER_MODEL_TYPES,
    write_json_payload,
)
from this_voice_thing.ui.pages.advanced import AdvancedPage
from this_voice_thing.ui.pages.api_server import ApiServer
from this_voice_thing.ui.pages.discover import Discover
from this_voice_thing.ui.pages.documents import Documents
from this_voice_thing.ui.pages.engine_controls import EngineControls
from this_voice_thing.ui.pages.estimates import Estimates
from this_voice_thing.ui.pages.finishing import Finishing
from this_voice_thing.ui.pages.generate import GeneratePage
from this_voice_thing.ui.pages.generation import Generation
from this_voice_thing.ui.pages.library import Library
from this_voice_thing.ui.pages.live_voice import LiveVoicePage
from this_voice_thing.ui.pages.model_loading import ModelLoading
from this_voice_thing.ui.pages.model_settings import ModelSettings
from this_voice_thing.ui.pages.models import ModelPage
from this_voice_thing.ui.pages.player import Player
from this_voice_thing.ui.pages.pronunciations import Pronunciations
from this_voice_thing.ui.pages.recording import Recording
from this_voice_thing.ui.pages.studio import StudioPage
from this_voice_thing.ui.pages.transcribe import TranscribePage
from this_voice_thing.ui.pages.voice import VoicePage
from this_voice_thing.ui.pages.voice_picker import VoicePicker
from this_voice_thing.ui.pages.voice_use import VoiceUse


class ChatterboxApp(GeneratePage, Generation, Documents, Estimates, Finishing, EngineControls, Player,
                    LiveVoicePage, StudioPage, VoicePage, VoicePicker, Library, VoiceUse, Recording, TranscribePage,
                    ModelPage, Discover, ModelLoading, ModelSettings, AdvancedPage, ApiServer, Pronunciations, QMainWindow):
    log_message_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(ui_theme.APP_NAME)
        self.setWindowIcon(ui_theme.app_icon())
        self.resize(1000, 820)
        self.model = None
        self.device_used = "cpu"
        self.current_model_repo = DEFAULT_MODEL_REPO
        self.current_model_backend = BACKEND_MULTILINGUAL
        self.current_multilingual_t3_model = resolve_multilingual_t3_model(DEFAULT_MULTILINGUAL_T3_MODEL)
        self.selected_model_repo = DEFAULT_MODEL_REPO
        self.system_has_nvidia_gpu = has_system_nvidia_gpu()
        self.cuda_runtime_issue = None
        self.script_dir = paths.ROOT  # app data stays in the project folder
        self.model_config_path = os.path.join(self.script_dir, MODEL_CONFIG_FILENAME)
        self.app_settings_path = os.path.join(self.script_dir, APP_SETTINGS_FILENAME)
        self.model_entries = load_models_config(self.model_config_path)
        self.app_settings = self.load_app_settings()
        self.apply_hf_token_setting()
        self.current_audio_file = None
        self.output_directory = os.path.join(os.getcwd(), "chatterbox_outputs")
        if not os.path.exists(self.output_directory):
            os.makedirs(self.output_directory)
        self.last_reference_audio_dir = self.script_dir
        self.recordings_directory = os.path.join(
            self.script_dir, REFERENCE_RECORDINGS_DIRNAME)
        self.voice_library = voice_library.VoiceLibrary(self.script_dir, self.recordings_directory)
        self.pronunciations = pronunciation.Dictionary(os.path.join(self.script_dir, pronunciation.FILENAME))
        self.api_settings = dict({"enabled": False, "port": local_api.DEFAULT_PORT, "token": ""},
                                 **self.app_settings.get("api", {}))
        self.api_server = None
        self.api_bridge = ApiBridge(self)
        self.api_busy = False
        self.api_loading_entry = None
        self.google_account = google_docs.GoogleAccount(self.script_dir)
        self.transcriber = transcription.Transcriber()
        self.transcript = None
        self.active_voice_id = None
        self.pending_voice = None
        self.media_devices = QMediaDevices(self)
        self.recording_format = None
        self.recording_buffer = bytearray()

        self.preview_player = QMediaPlayer(self)
        self.preview_audio_output = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio_output)
        self.preview_button_playing = None
        self.preview_player.playbackStateChanged.connect(self._on_preview_state_changed)

        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.is_seeking_audio = False
        self.paused_position = 0  # Added paused_position here

        self.media_player.positionChanged.connect(self.update_slider_position)
        self.media_player.durationChanged.connect(self.update_duration_info)
        self.media_player.playbackStateChanged.connect(
            self.handle_playback_state_changed)
        self.media_player.errorOccurred.connect(self.handle_media_error)

        self.generation_timer = QTimer(self)
        self.generation_timer.timeout.connect(
            self.update_generation_time_display)  # Renamed for clarity
        self.generation_start_time = None
        self.is_generating = False
        self.model_is_warm = False
        self.generation_is_preview = False
        self.generation_started_at = None
        self.generation_char_count = 0
        self.current_document_name = None
        self.last_preview_seed = None
        self.text_stats_timer = QTimer(self)
        self.text_stats_timer.setSingleShot(True)
        self.text_stats_timer.setInterval(350)
        self.text_stats_timer.timeout.connect(self.update_text_stats)
        sampling = self.app_settings.get("sampling", {})
        self.repetition_penalty = float(sampling.get("repetition_penalty", 1.2))
        self.min_p = float(sampling.get("min_p", 0.05))
        self.top_p = float(sampling.get("top_p", 1.0))

        self.log_message_signal.connect(self.append_console_log)
        self._init_ui()
        self.fit_default_geometry()
        self.restore_window_settings()
        self.update_minimum_size()
        self.attach_log_sink()
        self.update_output_log()
        if CHATTERBOX_AVAILABLE:
            if self.system_has_nvidia_gpu and not torch.cuda.is_available():
                print(
                    "WARNING: NVIDIA GPU detected, but the installed PyTorch build "
                    "does not have CUDA enabled. The app will run on CPU."
                )
            self.set_status_message(
                "Status: App ready. Default model load will start shortly. First run may download model files and can take several minutes."
            )
            QTimer.singleShot(0, self.start_default_model_load)
        else:
            self.set_status_message("Status: Chatterbox library not found.")
            self.generate_button.setEnabled(False)
        QTimer.singleShot(0, self.restart_api_server)

    PAGE_GENERATE, PAGE_LIVE_VOICE, PAGE_STUDIO, PAGE_VOICE, PAGE_TRANSCRIBE, PAGE_MODEL, PAGE_ADVANCED, PAGE_LOG = range(8)

    def _make_card(self, title=None):
        card = ui_theme.CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            layout.addWidget(title_label)
        return card, layout

    def _make_page(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        # Cards carry their own shadow margin, so page spacing is tighter.
        layout.setContentsMargins(17, 14, 17, 10)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("PageSubtitle")
        # Line up with the cards' visible edge (inside their shadow margin).
        for label in (title_label, subtitle_label):
            label.setContentsMargins(ui_theme.SHADOW, 0, ui_theme.SHADOW, 0)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(6)
        return page, layout

    @staticmethod
    def _accent(button):
        button.setProperty("accent", True)
        return button

    @staticmethod
    def _link(button):
        button.setFlat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        root_layout = QHBoxLayout(main_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar_panel = ui_theme.SidebarPanel()
        sidebar_panel.setObjectName("SidebarPanel")
        sidebar_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar_layout = QVBoxLayout(sidebar_panel)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(180)
        self.sidebar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for label in ("Generate", "Live Voice", "Studio", "Voices", "Transcribe", "Model", "Advanced", "Log"):
            self.sidebar.addItem(QListWidgetItem(label))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(18, 16, 12, 10)
        title_row.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(ui_theme.app_icon().pixmap(26, 26))
        title_row.addWidget(logo)
        app_title = QLabel(ui_theme.APP_NAME)
        app_title.setObjectName("AppTitle")
        app_title.setToolTip(f"{ui_theme.TAGLINE}\nBegan as a fork of AcTePuKc/Chatterbox-TTS-UI; "
                             "Resemble AI's Chatterbox is one of its engines.")
        title_row.addWidget(app_title)
        title_row.addStretch(1)
        sidebar_layout.addLayout(title_row)
        sidebar_layout.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self.on_page_changed)
        content_area = ui_theme.TexturedArea()
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.pages)
        root_layout.addWidget(sidebar_panel)
        root_layout.addWidget(content_area, 1)

        self._build_generate_page()
        self._build_live_voice_page()
        self._build_studio_page()
        self._build_voice_page()
        self._build_transcribe_page()
        self._build_model_page()
        self._build_advanced_page()
        self._build_log_page()

        self.sidebar.setCurrentRow(self.PAGE_GENERATE)

        qt_status_bar = self.statusBar()
        qt_status_bar.setSizeGripEnabled(False)
        self.status_bar = QLabel("Status: Initializing...")
        self.status_bar.setWordWrap(False)
        self.status_bar.setTextFormat(Qt.TextFormat.PlainText)
        # Status text is elided to fit; it must never set the window's minimum width.
        self.status_bar.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.status_bar.setMinimumWidth(120)
        self.status_bar.setFixedHeight(self.status_bar.sizeHint().height() + 4)
        qt_status_bar.addWidget(self.status_bar, 1)
        self.model_load_progress = QProgressBar()
        self.model_load_progress.setRange(0, 1)
        self.model_load_progress.setValue(0)
        self.model_load_progress.setTextVisible(False)
        self.model_load_progress.setFixedHeight(8)
        self.model_load_progress.setFixedWidth(120)
        self.model_load_progress.setEnabled(False)
        qt_status_bar.addPermanentWidget(self.model_load_progress)
        self.refresh_model_repo_options()
        self.refresh_language_options()
        self.refresh_models_page()
        self.update_hf_token_status()
        self.set_tuning_expanded(bool(self.app_settings.get("tuning_expanded", False)))
        self.refresh_recordings_list()
        self.set_reference_audio(None)

    def _build_log_page(self):
        log_page, log_layout = self._make_page(
            "Log", "Technical output from model loading and generation.")
        self.console_log_view = QPlainTextEdit()
        self.console_log_view.setReadOnly(True)
        self.console_log_view.setMaximumBlockCount(1000)
        self.console_log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.console_log_view.setObjectName("LogView")
        log_layout.addWidget(self.console_log_view, 1)
        self.pages.addWidget(log_page)

    def attach_log_sink(self):
        # the console tee in common forwards to this sink
        common.APP_LOG_SINK = self.log_message_signal.emit

    def append_console_log(self, text):
        if not hasattr(self, "console_log_view"):
            return
        cursor = self.console_log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(str(text))
        self.console_log_view.setTextCursor(cursor)
        self.console_log_view.ensureCursorVisible()

    def load_app_settings(self):
        payload, load_error = read_json_payload(self.app_settings_path)
        if load_error:
            print(load_error)
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def update_minimum_size(self):
        """Keep the window at least as large as its content needs, so nothing
        ever clips or scrolls; grow the window if the content just got bigger."""
        if self.centralWidget() is None:
            return
        # Layout changes propagate upward one event-loop pass per level, so
        # invalidate the whole chain to measure the current content right now.
        for layout in self.findChildren(QLayout):
            layout.invalidate()
        self.layout().activate()
        hint = self.minimumSizeHint()
        self.setMinimumSize(hint)
        if self.isMaximized() or self.isFullScreen():
            return
        if self.width() < hint.width() or self.height() < hint.height():
            self.resize(max(self.width(), hint.width()), max(self.height(), hint.height()))

    def fit_default_geometry(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        hint = self.minimumSizeHint()
        width = max(hint.width(), min(self.width(), available.width() - 40))
        height = max(hint.height(), min(self.height(), available.height() - 60))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def restore_window_settings(self):
        geometry_b64 = self.app_settings.get("window_geometry")
        if isinstance(geometry_b64, str) and geometry_b64:
            try:
                geometry_bytes = base64.b64decode(geometry_b64.encode("ascii"))
                self.restoreGeometry(geometry_bytes)
            except Exception as exc:
                print(f"Failed to restore saved window geometry: {exc}")

        if self.app_settings.get("window_maximized"):
            self.showMaximized()
        elif self.app_settings.get("window_fullscreen"):
            self.showFullScreen()

    def save_window_settings(self):
        try:
            self.app_settings["window_geometry"] = base64.b64encode(
                bytes(self.saveGeometry())
            ).decode("ascii")
            self.app_settings["window_maximized"] = self.isMaximized()
            self.app_settings["window_fullscreen"] = self.isFullScreen()
        except Exception as exc:
            print(f"Failed to save window settings: {exc}")

    def save_app_settings(self):
        if hasattr(self, "speed_slider"):
            self.app_settings["finishing"] = self.current_finishing_settings().to_dict()
            self.app_settings["finishing_expanded"] = not self.finishing_panel.isHidden()
        try:
            write_json_payload(self.app_settings_path, self.app_settings)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Settings Error",
                f"Failed to save {APP_SETTINGS_FILENAME}: {exc}",
            )

    def set_status_message(self, message):
        compact_message = " ".join(str(message).split())
        self.status_bar.setToolTip(compact_message)
        metrics = self.status_bar.fontMetrics()
        elided_message = metrics.elidedText(
            compact_message,
            Qt.TextElideMode.ElideRight,
            max(120, self.status_bar.width() - 8),
        )
        self.status_bar.setText(elided_message)

    OPENAI_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"}

    def on_page_changed(self, page):
        if page == self.PAGE_LIVE_VOICE:
            self.refresh_live_voice_summary()
            self.refresh_live_audio_devices()
        if page == self.PAGE_STUDIO and not self.studio_busy:
            self._fill_studio_engines()  # models may have been added or installed meanwhile
        if page == self.PAGE_MODEL and getattr(self, "discover_results", None) is None \
                and getattr(self, "discover_thread", None) is None:
            self.start_discover()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        current_tooltip = self.status_bar.toolTip()
        if current_tooltip:
            self.set_status_message(current_tooltip)

    def closeEvent(self, event):
        if hasattr(self, "live_audio_output"):
            self.live_audio_output.stop()
        if getattr(self, "live_speech_thread", None) is not None:
            self.live_speech_thread.stop()
            self.live_speech_thread.wait()
        # the console tee in common forwards to this sink
        if self.api_server is not None:
            self.api_server.stop()
        if common.APP_LOG_SINK == self.log_message_signal.emit:
            common.APP_LOG_SINK = None
        self.save_window_settings()
        self.save_app_settings()
        if hasattr(self, 'model_loader_thread') and self.model_loader_thread.isRunning():
            self.model_loader_thread.quit()
            self.model_loader_thread.wait()
        if hasattr(self, 'audio_generator_thread') and self.audio_generator_thread.isRunning():
            self.audio_generator_thread.stop()  # Request stop
            self.audio_generator_thread.wait()  # Wait for it to finish
        if isinstance(self.model, WORKER_MODEL_TYPES):
            self.model.close()
        event.accept()




if __name__ == "__main__":
    taskbar.set_process_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(ui_theme.APP_NAME)
    app.setApplicationDisplayName(ui_theme.APP_NAME)
    ui_theme.apply_theme(app)
    window = ChatterboxApp()
    # Before show(): the taskbar button then never asks a busy window for its icon
    # (start-up work can block the UI thread, and Windows would cache the generic icon).
    taskbar.set_window_identity(window)
    window.show()
    sys.exit(app.exec())
