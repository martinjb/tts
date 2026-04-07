"""Audio player module — gTTS generation + pygame.mixer playback.

Speed control strategy
----------------------
Playback speed is adjusted by preprocessing the audio rather than
manipulating the mixer frequency.  The pipeline for speed != 1.0:

  mp3 bytes (from gTTS)
    → miniaudio.decode()           → raw signed-16 PCM at 44 100 Hz
    → numpy / pedalboard.time_stretch() → pitch-preserving time-stretch
    → wave module                  → WAV bytes in a BytesIO buffer
    → pygame.mixer.music.load()   → plays at correct speed, correct pitch

All audio data lives entirely in RAM — no temp files are written to disk.
Python's garbage collector reclaims the memory automatically when the
BytesIO / bytes objects are no longer referenced (eviction or app close).

pedalboard.time_stretch uses a phase-vocoder (Rubber Band library under
the hood) so pitch is preserved even at extreme speeds.

stretch_factor semantics (pedalboard convention):
  stretch_factor > 1  →  faster playback, shorter output
  stretch_factor < 1  →  slower playback, longer output
  i.e. stretch_factor == our "speed" multiplier directly.

Audio caching
-------------
*  ``_cached_mp3``   — raw MP3 bytes from gTTS; kept until the text
                       changes or the app closes.
*  ``_speed_cache``  — dict mapping each speed multiplier to its
                       pre-built pitch-preserving WAV bytes.  Rebuilt only
                       on first use at each speed; instant replay
                       thereafter.  Cleared when MP3 is evicted.

Speed steps
-----------
1.0x  2.0x

Cancel
------
Call ``cancel_generation()`` while a worker is running; the worker
discards the downloaded mp3 and emits ``generation_cancelled`` instead
of starting playback.  (The HTTP download itself cannot be aborted
mid-flight — cancel takes effect the moment the download finishes.)

Buffer
------
Call ``buffer(text)`` to pre-build the audio without starting playback.
The ``buffer_finished`` signal fires when the mp3 is ready.  Subsequent
``speak()`` calls with the same text will play instantly from cache.
"""

from __future__ import annotations

import io
import threading
import wave

import miniaudio
import numpy as np
import pedalboard
import pygame
from gtts import gTTS
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot


# Available playback speed multipliers (1x normal, 2x fast)
SPEED_STEPS   = [1.0, 2.0]
_SAMPLE_RATE  = 44100   # Hz — fixed mixer frequency
_DEFAULT_SPEED_IDX = 0


# ---------------------------------------------------------------------------
# Worker signals
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    started   = Signal()
    finished  = Signal(bytes)  # raw MP3 bytes
    cancelled = Signal()       # generation was cancelled by user
    error     = Signal(str)    # human-readable error message


class _SpeedWorkerSignals(QObject):
    finished = Signal(float, bytes)  # (speed, raw WAV bytes)
    error    = Signal(str)


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
        try:
            tts = gTTS(text=self._text, lang=self._language,
                       slow=self._slow, tld=self._tld)
            buf = io.BytesIO()
            tts.write_to_fp(buf)

            # Check cancel flag *after* the blocking download
            if self._cancel_flag.is_set():
                self.signals.cancelled.emit()
                return

            self.signals.finished.emit(buf.getvalue())
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Background speed-WAV builder
# ---------------------------------------------------------------------------

class SpeedWorker(QRunnable):
    """Builds a pitch-preserving WAV at the requested speed in a worker thread."""

    def __init__(
        self,
        mp3_data: bytes,
        speed: float,
        cancel_flag: threading.Event,
        builder,   # callable: (mp3_data: bytes, speed: float) -> bytes
    ) -> None:
        super().__init__()
        self._mp3_data    = mp3_data
        self._speed       = speed
        self._cancel_flag = cancel_flag
        self._builder     = builder
        self.signals      = _SpeedWorkerSignals()

    @Slot()
    def run(self) -> None:
        if self._cancel_flag.is_set():
            return
        try:
            wav_bytes = self._builder(self._mp3_data, self._speed)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
            return
        if self._cancel_flag.is_set():
            return
        self.signals.finished.emit(self._speed, wav_bytes)


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
    buffer_finished       = Signal()       # mp3 ready, playback NOT started
    speed_build_started   = Signal()       # SpeedWorker dispatched
    speed_build_finished  = Signal(float)  # WAV ready; playback beginning
    speed_build_error     = Signal(str)    # SpeedWorker failed

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
        self._cached_text: str | None   = None
        self._cached_mp3:  bytes | None = None          # raw MP3 bytes from gTTS
        self._speed_cache: dict[float, bytes] = {}      # speed → WAV bytes

        # State
        self._paused: bool      = False
        self._buffer_mode: bool = False  # True → next worker finish = buffer, not play

        # Playback position tracking for mid-playback speed changes.
        # _play_start_file_pos: seconds into the *current file* where play() was called.
        # get_pos() gives elapsed ms since that play() call (ignoring start offset),
        # so: current_file_pos = _play_start_file_pos + get_pos() / 1000.
        self._play_start_file_pos: float = 0.0
        # Stored start position for async SpeedWorker completions.
        self._pending_start_pos: float = 0.0

        # Cancel support
        self._cancel_flag       = threading.Event()
        self._speed_cancel_flag = threading.Event()

        # Poll for natural end-of-track so playback_finished can be emitted
        self._end_timer = QTimer(self)
        self._end_timer.setInterval(200)
        self._end_timer.timeout.connect(self._check_playback_ended)

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
        self._buffer_mode = False
        self._cancel_flag.clear()
        self._speed_cancel_flag.set()   # abort any in-progress speed build

        # Cache hit
        if text == self._cached_text and self._cached_mp3 is not None:
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

    def buffer(self, text: str) -> None:
        """Pre-build TTS audio for *text* without starting playback.

        Emits ``buffer_finished`` when the mp3 is ready.  A subsequent
        ``speak()`` call with the same text will play instantly from cache.
        """
        text = text.strip()
        if not text:
            return

        self._stop_playback()
        self._paused = False
        self._cancel_flag.clear()
        self._speed_cancel_flag.set()   # abort any in-progress speed build
        self._buffer_mode = True

        # Cache hit — already buffered
        if text == self._cached_text and self._cached_mp3 is not None:
            self.cache_used.emit()
            self.buffer_finished.emit()
            return

        # Cache miss — generate mp3 without playing
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
        self._buffer_mode = False

    def pause(self) -> None:
        """Pause playback."""
        if not self._paused and pygame.mixer.get_init():
            pygame.mixer.music.pause()
            self._paused = True

    def play(self) -> None:
        """Resume from paused position, or replay from cache if stopped."""
        if self._paused:
            if pygame.mixer.get_init():
                pygame.mixer.music.unpause()
                self._paused = False
        elif self.has_cache:
            self._play_from_cache()

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

    def toggle_speed(self) -> float:
        """Toggle between 1x and 2x. Returns new speed."""
        self._speed_idx = 1 - self._speed_idx
        self._apply_speed_change()
        return self.speed

    # State ---------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        """True if music is actively playing (not paused, not stopped)."""
        return (not self._paused
                and pygame.mixer.get_init()
                and pygame.mixer.music.get_busy())

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def has_cache(self) -> bool:
        """True if MP3 bytes are in memory and ready to play."""
        return self._cached_mp3 is not None

    def is_text_cached(self, text: str) -> bool:
        return text.strip() == self._cached_text and self.has_cache

    # ------------------------------------------------------------------
    # Speed preprocessing
    # ------------------------------------------------------------------

    def _build_speed_wav(self, mp3_data: bytes, speed: float) -> bytes:
        """Decode mp3 bytes → pitch-preserving time-stretch → return WAV bytes.

        Uses miniaudio to decode the mp3 to raw PCM, then pedalboard's
        phase-vocoder time_stretch to change speed without altering pitch.

        pedalboard stretch_factor convention:
          > 1.0  →  faster (shorter output)
          < 1.0  →  slower (longer output)
        i.e. stretch_factor == our speed multiplier directly.
        """
        decoded = miniaudio.decode(
            mp3_data,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2,
            sample_rate=_SAMPLE_RATE,
        )
        nch = decoded.nchannels
        sr  = _SAMPLE_RATE

        # Convert raw PCM bytes → int16 → float32 (channels x samples)
        pcm_int16  = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        # interleaved stereo → reshape to (nframes, nch), then transpose to (nch, nframes)
        pcm_float  = pcm_int16.reshape(-1, nch).T.astype(np.float32) / 32768.0

        # Pitch-preserving time stretch (pedalboard phase vocoder)
        stretched  = pedalboard.time_stretch(pcm_float, float(sr), stretch_factor=float(speed))

        # Convert back to interleaved int16
        pcm_out    = (stretched.T * 32767.0).clip(-32768, 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(nch)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm_out.tobytes())

        return buf.getvalue()

    def _play_from_cache(self, start_pos: float = 0.0) -> None:
        """Play from cached bytes, dispatching a SpeedWorker if WAV not cached."""
        assert self._cached_mp3 is not None
        if abs(self.speed - 1.0) < 1e-9:
            # Native speed — play the mp3 directly (no WAV needed)
            self._play_file(self._cached_mp3, start_pos=start_pos)
        elif self.speed in self._speed_cache:
            # WAV already built for this speed — play immediately
            self._play_file(self._speed_cache[self.speed], start_pos=start_pos)
        else:
            # Dispatch background WAV builder; store start_pos for when it finishes
            self._pending_start_pos = start_pos
            self._speed_cancel_flag.clear()
            worker = SpeedWorker(
                mp3_data=self._cached_mp3,
                speed=self.speed,
                cancel_flag=self._speed_cancel_flag,
                builder=self._build_speed_wav,
            )
            worker.signals.finished.connect(self._on_speed_worker_finished)
            worker.signals.error.connect(self._on_speed_worker_error)
            self.speed_build_started.emit()
            self._pool.start(worker)

    def _apply_speed_change(self) -> None:
        """Switch to the new speed. Only replays if audio was actively playing;
        paused or stopped state is preserved. Resumes from the equivalent
        content position in the new-speed file."""
        if not self.has_cache:
            return
        was_playing = self.is_playing

        if was_playing:
            # Compute current position in the *original content* timeline.
            # _play_start_file_pos: where in the current file we started (seconds).
            # get_pos(): elapsed ms since that play() call (does not include start offset).
            elapsed_real = pygame.mixer.music.get_pos() / 1000.0
            current_file_pos = self._play_start_file_pos + elapsed_real
            # Old speed is the opposite index since toggle already updated _speed_idx.
            old_speed = SPEED_STEPS[1 - self._speed_idx]
            content_pos = current_file_pos * old_speed
            # Convert to file position in the new-speed file.
            new_file_pos = max(0.0, content_pos / self.speed)

        self._speed_cancel_flag.set()
        self._stop_playback()
        if was_playing:
            self._paused = False
            self._play_from_cache(start_pos=new_file_pos)

    # ------------------------------------------------------------------
    # Low-level playback
    # ------------------------------------------------------------------

    def _play_file(self, data: bytes, start_pos: float = 0.0) -> None:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=2, buffer=512)
            pygame.mixer.music.load(io.BytesIO(data))
            pygame.mixer.music.set_volume(self.volume)
            pygame.mixer.music.play(start=start_pos)
            self._play_start_file_pos = start_pos
            self._end_timer.start()
            self.playback_started.emit()
        except Exception as exc:  # noqa: BLE001
            self.playback_error.emit(str(exc))

    def _stop_playback(self) -> None:
        self._end_timer.stop()
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

    def _check_playback_ended(self) -> None:
        """Called by QTimer; emits playback_finished when track ends naturally."""
        if not self._paused and pygame.mixer.get_init() and not pygame.mixer.music.get_busy():
            self._end_timer.stop()
            self.playback_finished.emit()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _evict_cache(self) -> None:
        """Clear all cached audio from memory."""
        self._speed_cancel_flag.set()
        self._speed_cache.clear()
        self._cached_mp3  = None
        self._cached_text = None

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_worker_started(self) -> None:
        self.generation_started.emit()

    @Slot(bytes)
    def _on_worker_finished(self, mp3_bytes: bytes) -> None:
        self._cached_mp3 = mp3_bytes
        if self._buffer_mode:
            self._buffer_mode = False
            self.buffer_finished.emit()
        else:
            self._play_from_cache()
        # Always pre-build the 2x WAV in the background for instant switching
        self._prebuild_2x()

    def _prebuild_2x(self) -> None:
        """Dispatch a background SpeedWorker to pre-build the 2x WAV."""
        fast = SPEED_STEPS[-1]  # 2.0
        if self._cached_mp3 is None:
            return
        if fast in self._speed_cache:
            return  # already cached
        self._speed_cancel_flag.clear()
        worker = SpeedWorker(
            mp3_data=self._cached_mp3,
            speed=fast,
            cancel_flag=self._speed_cancel_flag,
            builder=self._build_speed_wav,
        )
        worker.signals.finished.connect(self._on_speed_worker_finished)
        worker.signals.error.connect(self._on_speed_worker_error)
        self._pool.start(worker)

    @Slot()
    def _on_worker_cancelled(self) -> None:
        self._cached_text = None
        self._buffer_mode = False
        self.generation_cancelled.emit()

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._cached_text = None
        self._buffer_mode = False
        self.playback_error.emit(message)

    @Slot(float, bytes)
    def _on_speed_worker_finished(self, speed: float, wav_bytes: bytes) -> None:
        self._speed_cache[speed] = wav_bytes
        # Only play if this speed is still the current one
        if abs(speed - self.speed) < 1e-9:
            self._play_file(wav_bytes, start_pos=self._pending_start_pos)
            self.speed_build_finished.emit(speed)

    @Slot(str)
    def _on_speed_worker_error(self, message: str) -> None:
        self.speed_build_error.emit(f"Speed processing error: {message}")

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
