"""Audio player module — gTTS generation + pygame.mixer playback.

Speed control strategy
----------------------
Playback speed is adjusted by preprocessing the audio rather than
manipulating the mixer frequency.  The pipeline for speed != 1.0:

  mp3 (from gTTS)
    → miniaudio.decode_file()      → raw signed-16 PCM at 44 100 Hz
    → numpy / pedalboard.time_stretch() → pitch-preserving time-stretch
    → wave module                  → temp WAV at 44 100 Hz
    → pygame.mixer.music.load()   → plays at correct speed, correct pitch

pedalboard.time_stretch uses a phase-vocoder (Rubber Band library under
the hood) so pitch is preserved even at extreme speeds.

stretch_factor semantics (pedalboard convention):
  stretch_factor > 1  →  faster playback, shorter output
  stretch_factor < 1  →  slower playback, longer output
  i.e. stretch_factor == our "speed" multiplier directly.

Audio caching
-------------
*  ``_cached_path``  — original mp3 from gTTS; kept until the text
                       changes or the app closes.
*  ``_speed_path``   — temp WAV built from the cached mp3 at the
                       current speed; rebuilt whenever speed changes.
   Stop does NOT delete either file so the next speak() call with
   the same text replays instantly.

Speed steps
-----------
0.5 ×  0.75 ×  1.0 ×  1.25 ×  1.5 ×  2.0 ×

Cancel
------
Call ``cancel_generation()`` while a worker is running; the worker
discards the downloaded mp3 and emits ``generation_cancelled`` instead
of starting playback.  (The HTTP download itself cannot be aborted
mid-flight — cancel takes effect the moment the download finishes.)
"""

from __future__ import annotations

import os
import tempfile
import threading
import wave
from pathlib import Path

import miniaudio
import numpy as np
import pedalboard
import pygame
from gtts import gTTS
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


# Available playback speed multipliers
SPEED_STEPS   = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
_SAMPLE_RATE  = 44100   # Hz — fixed mixer frequency
_DEFAULT_SPEED_IDX = SPEED_STEPS.index(1.0)


# ---------------------------------------------------------------------------
# Worker signals
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    started   = Signal()
    finished  = Signal(str)    # path to temp mp3
    cancelled = Signal()       # generation was cancelled by user
    error     = Signal(str)    # human-readable error message


# ---------------------------------------------------------------------------
# Background TTS worker
# ---------------------------------------------------------------------------

class TTSWorker(QRunnable):
    """Runs gTTS in a thread-pool worker so the UI never blocks."""

    def __init__(
        self,
        text: str,
        language: str = "en",
        slow: bool = False,
        tld: str = "com",
        cancel_flag: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._text        = text
        self._language    = language
        self._slow        = slow
        self._tld         = tld
        self._cancel_flag = cancel_flag or threading.Event()
        self.signals      = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        self.signals.started.emit()
        tmp_path: str | None = None
        try:
            tts = gTTS(text=self._text, lang=self._language,
                       slow=self._slow, tld=self._tld)
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            tmp_path = tmp.name
            tts.save(tmp_path)

            # Check cancel flag *after* the blocking download
            if self._cancel_flag.is_set():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                self.signals.cancelled.emit()
                return

            self.signals.finished.emit(tmp_path)
        except Exception as exc:  # noqa: BLE001
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class TTSPlayer(QObject):
    """Manages TTS generation and pygame.mixer playback."""

    # Signals
    playback_started      = Signal()
    playback_finished     = Signal()
    playback_error        = Signal(str)
    generation_started    = Signal()
    generation_cancelled  = Signal()
    cache_used            = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

        # Playback settings
        self.language: str  = "en"
        self.slow: bool     = False
        self.tld: str       = "com"
        self.volume: float  = 1.0

        # Speed
        self._speed_idx: int = _DEFAULT_SPEED_IDX

        # Cache
        self._cached_text: str | None = None
        self._cached_path: str | None = None   # original mp3
        self._speed_path:  str | None = None   # speed-adjusted WAV

        # State
        self._paused: bool = False

        # Cancel support
        self._cancel_flag = threading.Event()

        pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=2, buffer=512)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Speak *text*, using the cache if text is unchanged."""
        text = text.strip()
        if not text:
            return

        self._stop_playback()
        self._paused = False
        self._cancel_flag.clear()   # reset cancel flag for new generation

        # Cache hit
        if (text == self._cached_text
                and self._cached_path
                and Path(self._cached_path).exists()):
            self.cache_used.emit()
            self._play_from_cache()
            return

        # Cache miss
        self._evict_cache()
        self._cached_text = text

        worker = TTSWorker(
            text=text,
            language=self.language,
            slow=self.slow,
            tld=self.tld,
            cancel_flag=self._cancel_flag,
        )
        worker.signals.started.connect(self._on_worker_started)
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.cancelled.connect(self._on_worker_cancelled)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def stop(self) -> None:
        """Stop playback. Cached audio files are kept for instant replay."""
        self._stop_playback()
        self._paused = False

    def cancel_generation(self) -> None:
        """Request cancellation of an in-progress generation.

        If the worker has already downloaded the audio, it will be discarded
        after the download completes.  Has no effect if not generating.
        """
        self._cancel_flag.set()

    def toggle_pause(self) -> bool:
        """Toggle pause/resume. Returns ``True`` if now paused."""
        if not pygame.mixer.get_init():
            return False
        if self._paused:
            pygame.mixer.music.unpause()
            self._paused = False
        else:
            pygame.mixer.music.pause()
            self._paused = True
        return self._paused

    # Speed ---------------------------------------------------------------

    @property
    def speed(self) -> float:
        return SPEED_STEPS[self._speed_idx]

    def speed_up(self) -> float:
        """Step speed up one notch. Returns new speed."""
        if self._speed_idx < len(SPEED_STEPS) - 1:
            self._speed_idx += 1
            self._apply_speed_change()
        return self.speed

    def speed_down(self) -> float:
        """Step speed down one notch. Returns new speed."""
        if self._speed_idx > 0:
            self._speed_idx -= 1
            self._apply_speed_change()
        return self.speed

    # State ---------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def has_cache(self) -> bool:
        """True if a valid cached audio file is ready to play."""
        return bool(self._cached_path and Path(self._cached_path).exists())

    def is_text_cached(self, text: str) -> bool:
        return text.strip() == self._cached_text and self.has_cache

    # ------------------------------------------------------------------
    # Speed preprocessing
    # ------------------------------------------------------------------

    def _build_speed_wav(self, mp3_path: str, speed: float) -> str:
        """Decode mp3 → pitch-preserving time-stretch → write temp WAV.

        Uses miniaudio to decode the mp3 to raw PCM, then pedalboard's
        phase-vocoder time_stretch to change speed without altering pitch.

        pedalboard stretch_factor convention:
          > 1.0  →  faster (shorter output)
          < 1.0  →  slower (longer output)
        i.e. stretch_factor == our speed multiplier directly.
        """
        decoded = miniaudio.decode_file(
            mp3_path,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2,
            sample_rate=_SAMPLE_RATE,
        )
        nch = decoded.nchannels
        sr  = _SAMPLE_RATE

        # Convert raw PCM bytes → int16 → float32 (channels × samples)
        pcm_int16  = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        # interleaved stereo → reshape to (nframes, nch), then transpose to (nch, nframes)
        pcm_float  = pcm_int16.reshape(-1, nch).T.astype(np.float32) / 32768.0

        # Pitch-preserving time stretch (pedalboard phase vocoder)
        stretched  = pedalboard.time_stretch(pcm_float, float(sr), stretch_factor=float(speed))

        # Convert back to interleaved int16
        pcm_out    = (stretched.T * 32767.0).clip(-32768, 32767).astype(np.int16)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(nch)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm_out.tobytes())

        return tmp.name

    def _play_from_cache(self) -> None:
        """Play from the cached mp3, applying speed preprocessing if needed."""
        assert self._cached_path
        if abs(self.speed - 1.0) < 1e-9:
            # Native speed — play the mp3 directly
            self._cleanup_speed_temp()
            self._play_file(self._cached_path)
        else:
            # Build (or rebuild) speed-adjusted WAV
            try:
                self._cleanup_speed_temp()
                self._speed_path = self._build_speed_wav(self._cached_path, self.speed)
                self._play_file(self._speed_path)
            except Exception as exc:  # noqa: BLE001
                self.playback_error.emit(f"Speed processing error: {exc}")

    def _apply_speed_change(self) -> None:
        """Replay from cache at the new speed (if cache is available)."""
        if self.has_cache:
            self._stop_playback()
            self._paused = False
            self._play_from_cache()

    # ------------------------------------------------------------------
    # Low-level playback
    # ------------------------------------------------------------------

    def _play_file(self, path: str) -> None:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=2, buffer=512)
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self.volume)
            pygame.mixer.music.play()
            self.playback_started.emit()
        except Exception as exc:  # noqa: BLE001
            self.playback_error.emit(str(exc))

    def _stop_playback(self) -> None:
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _evict_cache(self) -> None:
        """Delete cached files and clear metadata."""
        self._cleanup_speed_temp()
        if self._cached_path and Path(self._cached_path).exists():
            try:
                os.remove(self._cached_path)
            except OSError:
                pass
        self._cached_path = None
        self._cached_text = None

    def _cleanup_speed_temp(self) -> None:
        if self._speed_path and Path(self._speed_path).exists():
            try:
                os.remove(self._speed_path)
            except OSError:
                pass
        self._speed_path = None

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_worker_started(self) -> None:
        self.generation_started.emit()

    @Slot(str)
    def _on_worker_finished(self, path: str) -> None:
        self._cached_path = path
        self._play_from_cache()

    @Slot()
    def _on_worker_cancelled(self) -> None:
        self._cached_text = None
        self.generation_cancelled.emit()

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._cached_text = None
        self.playback_error.emit(message)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __del__(self) -> None:
        try:
            self._stop_playback()
            self._evict_cache()
            pygame.mixer.quit()
        except Exception:  # noqa: BLE001
            pass

