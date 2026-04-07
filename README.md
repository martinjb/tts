# TTS App

A simple desktop application that converts text or text files to speech using
Google Text-to-Speech (gTTS) and PySide6.

---

## Features

- Type or paste text into the editor, then click **Read Text** to hear it
- Load any `.txt` file and click **Read File** to hear it read aloud
- Stop or pause/resume playback at any time
- Configurable language, accent, speed, and volume via `settings.ini`

---

## Requirements

- Python 3.9+
- Internet connection (gTTS calls the Google TTS API)

---

## Installation
### From source (development)

```bash
git clone <repo-url>
cd tts
pip install -e .
tts-app

### From PyPI (not yet published, so this will not work )

```bash
pip install tts-app
tts-app
```

```

---

## Configuration

Edit `settings.ini` to change behaviour without touching code:

| Section    | Key            | Default          | Description                          |
|------------|----------------|------------------|--------------------------------------|
| Playback   | language       | `en`             | BCP-47 language code                 |
| Playback   | slow           | `false`          | Speak slowly                         |
| Playback   | tld            | `com`            | Google TLD (affects accent)          |
| Playback   | volume         | `1.0`            | Volume 0.0 – 1.0                     |
| UI         | window_width   | `960`            | Initial window width (px)            |
| UI         | window_height  | `680`            | Initial window height (px)           |
| UI         | stylesheet     | `resources/style.qss` | Path to QSS file              |
| UI         | icon           | `resources/icon.ico`  | Path to window icon           |
| File       | last_dir       | *(empty)*        | Last used directory (auto-saved)     |

---

## Replacing the Icon

Drop your own `icon.ico` into the `resources/` folder to replace the placeholder.

---

## License

MIT
