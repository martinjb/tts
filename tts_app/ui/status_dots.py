"""Status indicator dot widgets for the TTS App.

Two dots are shown above the text editor:
  • Content dot  — reflects whether cached audio matches current text
  • Progress dot — reflects audio-generation activity

Dot states
----------
DARK   : inactive / default (dim grey)
GREEN  : good / ready
YELLOW : stale / needs attention
RED    : error
ORANGE : working / in-progress
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

class DotState:
    DARK   = "dark"
    GREEN  = "green"
    YELLOW = "yellow"
    RED    = "red"
    ORANGE = "orange"


# Colour map  { state: (fill_hex, border_hex) }
_COLORS: dict[str, tuple[str, str]] = {
    DotState.DARK:   ("#3a3a50", "#2a2a3d"),
    DotState.GREEN:  ("#a6e3a1", "#5a9e56"),
    DotState.YELLOW: ("#f9e2af", "#c9a34f"),
    DotState.RED:    ("#f38ba8", "#b84060"),
    DotState.ORANGE: ("#fab387", "#c06830"),
}

_DOT_SIZE = 14   # diameter in pixels
_GLOW_EXTRA = 4  # extra radius for the soft glow ring on active states


# ---------------------------------------------------------------------------
# Single dot widget
# ---------------------------------------------------------------------------

class DotWidget(QWidget):
    """A small circular status indicator painted with QPainter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: str = DotState.DARK
        total = _DOT_SIZE + _GLOW_EXTRA * 2
        self.setFixedSize(total, total)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in _COLORS:
            state = DotState.DARK
        if self._state != state:
            self._state = state
            self.update()

    @property
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        fill_hex, border_hex = _COLORS[self._state]
        fill   = QColor(fill_hex)
        border = QColor(border_hex)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width()  // 2
        cy = self.height() // 2
        r  = _DOT_SIZE // 2

        # Soft glow for active states
        if self._state != DotState.DARK:
            glow = QColor(fill_hex)
            for step in range(_GLOW_EXTRA, 0, -1):
                glow.setAlpha(int(60 * (step / _GLOW_EXTRA)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(cx - r - step, cy - r - step,
                                    (r + step) * 2, (r + step) * 2)

        # Main dot
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Inner highlight (top-left gleam)
        if self._state != DotState.DARK:
            gleam = QColor(255, 255, 255, 80)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gleam)
            hr = max(2, r // 3)
            painter.drawEllipse(cx - r + 2, cy - r + 2, hr, hr)

        painter.end()


# ---------------------------------------------------------------------------
# Labelled dot (dot + text label side by side)
# ---------------------------------------------------------------------------

class LabelledDot(QWidget):
    """A DotWidget with a small text label to its right."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._dot = DotWidget()
        layout.addWidget(self._dot)

        self._lbl = QLabel(label)
        self._lbl.setObjectName("dotLabel")
        self._lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._lbl)

    def set_state(self, state: str) -> None:
        self._dot.set_state(state)

    @property
    def state(self) -> str:
        return self._dot.state


# ---------------------------------------------------------------------------
# StatusIndicator — the composite bar shown above the editor
# ---------------------------------------------------------------------------

class StatusIndicator(QWidget):
    """Row of two named dots: content status and generation progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusIndicator")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(16)

        self._content  = LabelledDot("Audio")
        self._progress = LabelledDot("Generating")

        layout.addWidget(self._content)
        layout.addWidget(self._progress)
        layout.addStretch()

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------
    # Convenience setters
    # ------------------------------------------------------------------

    def set_content_state(self, state: str) -> None:
        self._content.set_state(state)

    def set_progress_state(self, state: str) -> None:
        self._progress.set_state(state)

    @property
    def content_state(self) -> str:
        return self._content.state

    @property
    def progress_state(self) -> str:
        return self._progress.state
