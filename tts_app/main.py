"""Entry point for the TTS App."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from tts_app.config.settings import Settings
from tts_app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("TTS App")
    app.setOrganizationName("tts-app")

    settings = Settings()

    # Apply stylesheet
    qss_path = settings.get_stylesheet_path()
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    window = MainWindow(settings=settings)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
