"""Settings dialog — edit settings.ini values through the UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tts_app.config.settings import Settings


# Common language codes offered as quick presets
_LANGUAGE_PRESETS = [
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Chinese (Mandarin)", "zh-CN"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Russian", "ru"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
]

_TLD_OPTIONS = [
    ("com — Global / US", "com"),
    ("co.uk — British English", "co.uk"),
    ("com.au — Australian English", "com.au"),
    ("ca — Canadian", "ca"),
    ("co.in — Indian English", "co.in"),
    ("ie — Irish English", "ie"),
    ("co.za — South African", "co.za"),
]


class SettingsDialog(QDialog):
    """Modal dialog for editing tts_app settings.

    Changes are only written to disk when the user clicks Save.
    """

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.setModal(True)

        self._build_ui()
        self._load_current_values()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ---- Playback group ----
        pb_group = QGroupBox("Playback")
        pb_form  = QFormLayout(pb_group)
        pb_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        pb_form.setSpacing(8)

        # Language
        lang_widget = QWidget()
        lang_row    = QHBoxLayout(lang_widget)
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(6)

        self._lang_edit = QLineEdit()
        self._lang_edit.setPlaceholderText("e.g. en")
        self._lang_edit.setMaximumWidth(60)
        self._lang_edit.setToolTip("BCP-47 language code")

        self._lang_preset = QComboBox()
        self._lang_preset.setToolTip("Quick-select a common language")
        for label, code in _LANGUAGE_PRESETS:
            self._lang_preset.addItem(label, userData=code)
        self._lang_preset.currentIndexChanged.connect(self._on_lang_preset_changed)

        lang_row.addWidget(self._lang_edit)
        lang_row.addWidget(QLabel("or"))
        lang_row.addWidget(self._lang_preset, stretch=1)
        pb_form.addRow("Language code:", lang_widget)

        # TLD / Accent
        self._tld_combo = QComboBox()
        for label, tld in _TLD_OPTIONS:
            self._tld_combo.addItem(label, userData=tld)
        self._tld_combo.setToolTip(
            "Controls regional accent (only affects language=en variants)"
        )
        pb_form.addRow("Accent / TLD:", self._tld_combo)

        # Slow speech
        self._slow_check = QCheckBox("Speak slowly")
        self._slow_check.setToolTip("Halves the speaking speed")
        pb_form.addRow("Speed:", self._slow_check)

        # Volume
        vol_widget = QWidget()
        vol_row    = QHBoxLayout(vol_widget)
        vol_row.setContentsMargins(0, 0, 0, 0)
        vol_row.setSpacing(8)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setTickInterval(10)
        self._vol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)

        self._vol_label = QLabel("100%")
        self._vol_label.setFixedWidth(40)
        self._vol_slider.valueChanged.connect(
            lambda v: self._vol_label.setText(f"{v}%")
        )

        vol_row.addWidget(self._vol_slider, stretch=1)
        vol_row.addWidget(self._vol_label)
        pb_form.addRow("Volume:", vol_widget)

        root.addWidget(pb_group)

        # ---- UI / Window group ----
        ui_group = QGroupBox("Window")
        ui_form  = QFormLayout(ui_group)
        ui_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ui_form.setSpacing(8)

        size_widget = QWidget()
        size_row    = QHBoxLayout(size_widget)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(6)

        self._width_spin = QSpinBox()
        self._width_spin.setRange(400, 3840)
        self._width_spin.setSuffix(" px")

        self._height_spin = QSpinBox()
        self._height_spin.setRange(300, 2160)
        self._height_spin.setSuffix(" px")

        size_row.addWidget(QLabel("W"))
        size_row.addWidget(self._width_spin)
        size_row.addWidget(QLabel("H"))
        size_row.addWidget(self._height_spin)
        size_row.addStretch()
        ui_form.addRow("Starting size:", size_widget)

        note = QLabel("Window size takes effect on next launch.")
        note.setObjectName("settingsNote")
        note.setWordWrap(True)
        ui_form.addRow("", note)

        root.addWidget(ui_group)

        # ---- Buttons ----
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._save_and_close)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Populate controls from current settings
    # ------------------------------------------------------------------

    def _load_current_values(self) -> None:
        # Language
        lang = self._settings.get_language()
        self._lang_edit.setText(lang)
        # Try to select matching preset
        for i in range(self._lang_preset.count()):
            if self._lang_preset.itemData(i) == lang:
                self._lang_preset.setCurrentIndex(i)
                break
        else:
            self._lang_preset.setCurrentIndex(-1)

        # TLD
        tld = self._settings.get_tld()
        for i in range(self._tld_combo.count()):
            if self._tld_combo.itemData(i) == tld:
                self._tld_combo.setCurrentIndex(i)
                break

        # Slow
        self._slow_check.setChecked(self._settings.get_slow())

        # Volume
        self._vol_slider.setValue(int(self._settings.get_volume() * 100))

        # Window size
        w, h = self._settings.get_window_size()
        self._width_spin.setValue(w)
        self._height_spin.setValue(h)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_lang_preset_changed(self, index: int) -> None:
        if index >= 0:
            code = self._lang_preset.itemData(index)
            self._lang_edit.setText(code)

    def _save_and_close(self) -> None:
        lang = self._lang_edit.text().strip() or "en"
        tld  = self._tld_combo.currentData() or "com"

        self._settings.set_language(lang)
        self._settings.set_tld(tld)
        self._settings.set_slow(self._slow_check.isChecked())
        self._settings.set_volume(self._vol_slider.value() / 100.0)
        self._settings.set_window_size(
            self._width_spin.value(),
            self._height_spin.value(),
        )
        self.accept()
