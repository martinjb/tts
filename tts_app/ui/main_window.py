"""Main window for the TTS App."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tts_app.audio.player import SPEED_STEPS, TTSPlayer
from tts_app.config.settings import Settings
from tts_app.ui.settings_dialog import SettingsDialog
from tts_app.ui.status_dots import DotState, StatusIndicator

# Tab indices
_TAB_CONTROLS = 0
_TAB_AUDIO    = 1


class MainWindow(QMainWindow):
    """Primary application window."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._player   = TTSPlayer(parent=self)
        self._loaded_file: Path | None = None

        # Apply playback settings from INI
        self._player.language = settings.get_language()
        self._player.slow     = settings.get_slow()
        self._player.tld      = settings.get_tld()
        self._player.volume   = settings.get_volume()

        # Track text at the time of last successful generation so we can tell
        # whether the editor content has drifted away from the cached audio.
        self._last_spoken_text: str | None = None

        # Window chrome
        w, h = settings.get_window_size()
        self.resize(w, h)
        self.setWindowTitle("TTS App")

        icon_path = settings.get_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._build_menu()
        self._connect_signals()

        # Generation progress timer (250ms ticks while generating)
        self._gen_timer = QTimer(self)
        self._gen_timer.setInterval(250)
        self._gen_timer.timeout.connect(self._on_gen_timer_tick)
        self._gen_start_time: float = 0.0
        self._dot_pulse_state: bool = True  # True = ORANGE, False = DARK

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 4)
        root_layout.setSpacing(4)

        # --- Status dots row (above editor) ---
        self._status_indicator = StatusIndicator()
        root_layout.addWidget(self._status_indicator)

        # --- Text editor ---
        self._editor = QTextEdit()
        self._editor.setObjectName("editor")
        self._editor.setPlaceholderText("Type or paste text here, then click 'Read Text'…")
        self._editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self._editor, stretch=1)

        # --- Tab widget ---
        self._tabs = QTabWidget()
        self._tabs.setObjectName("controlTabs")
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root_layout.addWidget(self._tabs, stretch=0)

        self._build_controls_tab()
        self._build_audio_tab()

        # Disable audio tab until first audio is ready
        self._tabs.setTabEnabled(_TAB_AUDIO, False)
        self._tabs.setTabToolTip(_TAB_AUDIO, "Generate audio first")

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self._status_bar.setObjectName("statusBar")
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    def _build_controls_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("controlsTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_choose = QPushButton("Choose File")
        self._btn_choose.setObjectName("btnChooseFile")
        self._btn_choose.setToolTip("Open a plain-text file")

        self._btn_read_text = QPushButton("Read Text")
        self._btn_read_text.setObjectName("btnReadText")
        self._btn_read_text.setToolTip("Speak the contents of the text editor")

        self._btn_read_file = QPushButton("Read File")
        self._btn_read_file.setObjectName("btnReadFile")
        self._btn_read_file.setToolTip("Speak the loaded file")
        self._btn_read_file.setEnabled(False)

        self._btn_cancel = QPushButton("✕  Cancel")
        self._btn_cancel.setObjectName("btnCancel")
        self._btn_cancel.setToolTip("Cancel audio generation in progress")
        self._btn_cancel.setVisible(False)

        for btn in (self._btn_choose, self._btn_read_text,
                    self._btn_read_file, self._btn_cancel):
            btn_row.addWidget(btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # File info row
        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        file_caption = QLabel("File:")
        file_caption.setObjectName("fileCaption")

        self._lbl_filename = QLabel("No file loaded")
        self._lbl_filename.setObjectName("lblFilename")

        self._lbl_stats = QLabel("")
        self._lbl_stats.setObjectName("lblStats")
        self._lbl_stats.setAlignment(Qt.AlignmentFlag.AlignRight)

        info_row.addWidget(file_caption)
        info_row.addWidget(self._lbl_filename, stretch=1)
        info_row.addWidget(self._lbl_stats)
        layout.addLayout(info_row)

        self._tabs.addTab(tab, "Controls")

    def _build_audio_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("audioTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setToolTip("Stop playback")

        self._btn_pause = QPushButton("⏸  Pause")
        self._btn_pause.setObjectName("btnPause")
        self._btn_pause.setToolTip("Pause / resume playback")

        # Speed controls
        self._btn_speed_down = QPushButton("◄◄")
        self._btn_speed_down.setObjectName("btnSpeedDown")
        self._btn_speed_down.setToolTip("Decrease playback speed")
        self._btn_speed_down.setFixedWidth(48)

        self._lbl_speed = QLabel(f"{self._player.speed:.2g}×")
        self._lbl_speed.setObjectName("lblSpeed")
        self._lbl_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_speed.setFixedWidth(48)
        self._lbl_speed.setToolTip("Current playback speed")

        self._btn_speed_up = QPushButton("►►")
        self._btn_speed_up.setObjectName("btnSpeedUp")
        self._btn_speed_up.setToolTip("Increase playback speed")
        self._btn_speed_up.setFixedWidth(48)

        for w in (self._btn_stop, self._btn_pause,
                  self._btn_speed_down, self._lbl_speed, self._btn_speed_up):
            btn_row.addWidget(w)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._tabs.addTab(tab, "Audio Controls")
        self._update_speed_buttons()
        self._set_audio_controls_enabled(False)

    # ------------------------------------------------------------------
    # Audio controls enable/disable helper
    # ------------------------------------------------------------------

    def _set_audio_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable all playback controls in the audio tab."""
        for w in (self._btn_stop, self._btn_pause,
                  self._btn_speed_down, self._btn_speed_up):
            w.setEnabled(enabled)
        # Speed label always visible but muted when disabled
        self._lbl_speed.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")

        act_open = QAction("Open File…", self)
        act_open.setShortcut("Ctrl+Q")
        act_open.triggered.connect(self._choose_file)
        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+X")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Settings menu (top-level)
        act_settings = QAction("Settings", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self._open_settings)
        menu_bar.addAction(act_settings)

        # Help menu
        help_menu = menu_bar.addMenu("Help")

        act_help = QAction("Show Help", self)
        act_help.setShortcut("F1")
        act_help.triggered.connect(self._show_help)
        help_menu.addAction(act_help)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Controls tab
        self._btn_choose.clicked.connect(self._choose_file)
        self._btn_read_text.clicked.connect(self._read_text)
        self._btn_read_file.clicked.connect(self._read_file)
        self._btn_cancel.clicked.connect(self._cancel_generation)

        # Audio tab
        self._btn_stop.clicked.connect(self._stop)
        self._btn_pause.clicked.connect(self._toggle_pause)
        self._btn_speed_down.clicked.connect(self._speed_down)
        self._btn_speed_up.clicked.connect(self._speed_up)

        # Player signals
        self._player.playback_started.connect(self._on_playback_started)
        self._player.playback_finished.connect(self._on_playback_finished)
        self._player.playback_error.connect(self._on_playback_error)
        self._player.generation_started.connect(self._on_generation_started)
        self._player.generation_cancelled.connect(self._on_generation_cancelled)
        self._player.cache_used.connect(self._on_cache_used)

        # Editor text change → update content dot
        self._editor.textChanged.connect(self._on_editor_text_changed)

    # ------------------------------------------------------------------
    # Slots — controls tab
    # ------------------------------------------------------------------

    @Slot()
    def _choose_file(self) -> None:
        last_dir = self._settings.get_last_dir() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Text File",
            last_dir,
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return

        file_path = Path(path)
        self._loaded_file = file_path
        self._settings.set_last_dir(str(file_path.parent))

        size_bytes = file_path.stat().st_size
        size_str   = self._format_size(size_bytes)

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{exc}")
            return

        word_count = len(text.split())
        self._lbl_filename.setText(file_path.name)
        self._lbl_stats.setText(f"Words: {word_count:,}  |  Size: {size_str}")
        self._btn_read_file.setEnabled(True)
        self._status_bar.showMessage(f"Loaded: {file_path.name}")

    @Slot()
    def _read_text(self) -> None:
        text = self._editor.toPlainText().strip()
        if not text:
            self._status_bar.showMessage("Nothing to read — type some text first.")
            return
        # If text has changed, hide the audio tab while generating new audio
        if not self._player.is_text_cached(text):
            self._set_audio_controls_enabled(False)
        self._tabs.setCurrentIndex(_TAB_CONTROLS)
        self._player.speak(text)

    @Slot()
    def _read_file(self) -> None:
        if self._loaded_file is None:
            self._status_bar.showMessage("No file loaded.")
            return
        try:
            text = self._loaded_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{exc}")
            return
        if not text.strip():
            self._status_bar.showMessage("File is empty.")
            return
        if not self._player.is_text_cached(text):
            self._set_audio_controls_enabled(False)
        self._tabs.setCurrentIndex(_TAB_CONTROLS)
        self._player.speak(text)

    # ------------------------------------------------------------------
    # Slots — audio tab
    # ------------------------------------------------------------------

    @Slot()
    def _stop(self) -> None:
        self._player.stop()
        self._btn_pause.setText("⏸  Pause")
        self._status_bar.showMessage("Stopped.")
        # Keep tab visible and controls enabled — cache still exists

    @Slot()
    def _toggle_pause(self) -> None:
        now_paused = self._player.toggle_pause()
        self._btn_pause.setText("▶  Resume" if now_paused else "⏸  Pause")
        self._status_bar.showMessage("Paused." if now_paused else "Resumed.")

    @Slot()
    def _speed_down(self) -> None:
        new_speed = self._player.speed_down()
        self._lbl_speed.setText(f"{new_speed:.2g}×")
        self._update_speed_buttons()
        self._status_bar.showMessage(f"Speed: {new_speed:.2g}×")

    @Slot()
    def _speed_up(self) -> None:
        new_speed = self._player.speed_up()
        self._lbl_speed.setText(f"{new_speed:.2g}×")
        self._update_speed_buttons()
        self._status_bar.showMessage(f"Speed: {new_speed:.2g}×")

    def _update_speed_buttons(self) -> None:
        idx = SPEED_STEPS.index(self._player.speed)
        # Only enable if audio controls are active (tab visible + cache exists)
        active = self._tabs.isTabEnabled(_TAB_AUDIO) and self._player.has_cache
        self._btn_speed_down.setEnabled(active and idx > 0)
        self._btn_speed_up.setEnabled(active and idx < len(SPEED_STEPS) - 1)

    @Slot()
    def _cancel_generation(self) -> None:
        self._player.cancel_generation()
        self._btn_cancel.setVisible(False)
        self._status_bar.showMessage("Cancelling…")

    # ------------------------------------------------------------------
    # Slots — player signals
    # ------------------------------------------------------------------

    @Slot()
    def _on_generation_started(self) -> None:
        self._btn_cancel.setVisible(True)
        self._set_audio_controls_enabled(False)
        # Start pulsing dot + elapsed timer
        self._gen_start_time = time.monotonic()
        self._dot_pulse_state = True
        self._status_indicator.set_progress_state(DotState.ORANGE)
        self._gen_timer.start()
        self._status_bar.showMessage("Generating audio…")

    @Slot()
    def _on_gen_timer_tick(self) -> None:
        elapsed = time.monotonic() - self._gen_start_time
        # Pulse dot between ORANGE and DARK
        self._dot_pulse_state = not self._dot_pulse_state
        state = DotState.ORANGE if self._dot_pulse_state else DotState.DARK
        self._status_indicator.set_progress_state(state)
        self._status_bar.showMessage(f"Generating audio… {elapsed:.1f}s")

    def _stop_gen_timer(self) -> None:
        self._gen_timer.stop()
        self._btn_cancel.setVisible(False)
        self._status_indicator.set_progress_state(DotState.DARK)

    @Slot()
    def _on_playback_started(self) -> None:
        self._stop_gen_timer()
        # Enable and switch to the audio tab on first playback
        self._tabs.setTabEnabled(_TAB_AUDIO, True)
        self._tabs.setTabToolTip(_TAB_AUDIO, "")
        self._tabs.setCurrentIndex(_TAB_AUDIO)
        self._set_audio_controls_enabled(True)
        self._update_speed_buttons()
        self._status_indicator.set_content_state(DotState.GREEN)
        self._btn_pause.setText("⏸  Pause")
        self._last_spoken_text = self._editor.toPlainText().strip()
        self._status_bar.showMessage("Playing…")

    @Slot()
    def _on_cache_used(self) -> None:
        self._stop_gen_timer()
        self._status_bar.showMessage("Using cached audio…")

    @Slot()
    def _on_generation_cancelled(self) -> None:
        self._stop_gen_timer()
        self._status_indicator.set_content_state(DotState.DARK)
        self._status_bar.showMessage("Generation cancelled.")

    @Slot()
    def _on_playback_finished(self) -> None:
        self._btn_pause.setText("⏸  Pause")
        self._status_bar.showMessage("Done.")

    @Slot(str)
    def _on_playback_error(self, message: str) -> None:
        self._stop_gen_timer()
        self._status_indicator.set_content_state(DotState.RED)
        self._btn_pause.setText("⏸  Pause")
        self._set_audio_controls_enabled(False)
        self._tabs.setTabEnabled(_TAB_AUDIO, False)
        self._tabs.setTabToolTip(_TAB_AUDIO, "Generate audio first")
        self._status_bar.showMessage(f"Error: {message}")
        QMessageBox.critical(self, "Playback Error", message)

    @Slot()
    def _on_editor_text_changed(self) -> None:
        current = self._editor.toPlainText().strip()
        if not current:
            self._status_indicator.set_content_state(DotState.DARK)
        elif self._player.is_text_cached(current):
            self._status_indicator.set_content_state(DotState.GREEN)
        else:
            self._status_indicator.set_content_state(DotState.YELLOW)

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    @Slot()
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, parent=self)
        if dlg.exec():
            # Apply any playback changes immediately (no restart needed)
            self._player.language = self._settings.get_language()
            self._player.slow     = self._settings.get_slow()
            self._player.tld      = self._settings.get_tld()
            self._player.volume   = self._settings.get_volume()
            self._status_bar.showMessage("Settings saved.")

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    @Slot()
    def _show_help(self) -> None:
        help_path = Path(__file__).parent.parent.parent / "help.txt"
        content = help_path.read_text(encoding="utf-8") if help_path.exists() else "Help file not found."
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Help")
        dlg.setText(content)
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.exec()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / 1024 ** 2:.2f} MB"

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._player.stop()
        event.accept()
