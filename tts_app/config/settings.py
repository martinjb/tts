"""Settings module — thin wrapper around configparser for tts_app."""

from __future__ import annotations

import configparser
import os
from pathlib import Path


def _find_settings_ini() -> Path:
    """Locate settings.ini next to the project root (dev) or in the CWD."""
    # When running from source the file lives two levels above this module:
    #   tts_app/config/settings.py  →  ../../settings.ini
    candidates = [
        Path(__file__).parent.parent.parent / "settings.ini",
        Path.cwd() / "settings.ini",
    ]
    for path in candidates:
        if path.exists():
            return path
    # Fall back to the first candidate even if it doesn't exist yet
    return candidates[0]


class Settings:
    """Read and write settings.ini values."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _find_settings_ini()
        self._cfg = configparser.ConfigParser()
        self._load()

    def _load(self) -> None:
        self._cfg.read(self._path, encoding="utf-8")

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            self._cfg.write(fh)

    # ------------------------------------------------------------------
    # [Playback]
    # ------------------------------------------------------------------

    def get_language(self) -> str:
        return self._cfg.get("Playback", "language", fallback="en")

    def get_slow(self) -> bool:
        return self._cfg.getboolean("Playback", "slow", fallback=False)

    def get_tld(self) -> str:
        return self._cfg.get("Playback", "tld", fallback="com")

    def get_volume(self) -> float:
        return max(0.0, min(1.0, self._cfg.getfloat("Playback", "volume", fallback=1.0)))

    # ------------------------------------------------------------------
    # [UI]
    # ------------------------------------------------------------------

    def get_window_size(self) -> tuple[int, int]:
        w = self._cfg.getint("UI", "window_width", fallback=960)
        h = self._cfg.getint("UI", "window_height", fallback=680)
        return (w, h)

    def get_stylesheet_path(self) -> Path:
        raw = self._cfg.get("UI", "stylesheet", fallback="resources/style.qss")
        p = Path(raw)
        if not p.is_absolute():
            p = self._path.parent / p
        return p

    def get_icon_path(self) -> Path:
        raw = self._cfg.get("UI", "icon", fallback="resources/icon.ico")
        p = Path(raw)
        if not p.is_absolute():
            p = self._path.parent / p
        return p

    # ------------------------------------------------------------------
    # [File]
    # ------------------------------------------------------------------

    def get_last_dir(self) -> str:
        return self._cfg.get("File", "last_dir", fallback="")

    def set_last_dir(self, directory: str) -> None:
        if not self._cfg.has_section("File"):
            self._cfg.add_section("File")
        self._cfg.set("File", "last_dir", directory)
        self._save()

    # ------------------------------------------------------------------
    # Setters (used by SettingsDialog)
    # ------------------------------------------------------------------

    def _ensure_section(self, section: str) -> None:
        if not self._cfg.has_section(section):
            self._cfg.add_section(section)

    def set_language(self, lang: str) -> None:
        self._ensure_section("Playback")
        self._cfg.set("Playback", "language", lang)
        self._save()

    def set_slow(self, slow: bool) -> None:
        self._ensure_section("Playback")
        self._cfg.set("Playback", "slow", "true" if slow else "false")
        self._save()

    def set_tld(self, tld: str) -> None:
        self._ensure_section("Playback")
        self._cfg.set("Playback", "tld", tld)
        self._save()

    def set_volume(self, volume: float) -> None:
        self._ensure_section("Playback")
        self._cfg.set("Playback", "volume", str(max(0.0, min(1.0, volume))))
        self._save()

    def set_window_size(self, width: int, height: int) -> None:
        self._ensure_section("UI")
        self._cfg.set("UI", "window_width", str(width))
        self._cfg.set("UI", "window_height", str(height))
        self._save()
