"""
Видео в Telegram: извлечь кадр и описать через vision; извлечь звук и распознать речь (Whisper),
чтобы агент «видел» и «слышал», что пользователь говорит.
"""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path


def _extract_frame(video_path: str | Path) -> Path | None:
    """Извлечь один кадр из середины видео, сохранить во временный JPEG. Возвращает путь или None."""
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        import cv2  # opencv-python
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        mid = max(0, total // 2)
        cap.set(cv2.CAP_PROP_POS_FRAMES, mid)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return None
        fd, out = tempfile.mkstemp(suffix=".jpg", prefix="video_frame_")
        os.close(fd)
        cv2.imwrite(out, frame)
        return Path(out)
    except Exception:
        return None


def extract_audio_from_video(video_path: str | Path) -> Path | None:
    """
    Извлечь звуковую дорожку из видео во временный WAV (для Whisper).
    Требуется ffmpeg в PATH. Возвращает путь к файлу или None.
    """
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="video_audio_")
        os.close(fd)
        out = Path(wav_path)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(out),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if not out.exists() or out.stat().st_size < 1000:
            out.unlink(missing_ok=True)
            return None
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def transcribe_video_audio(video_path: str | Path) -> str:
    """
    Извлечь звук из видео и распознать речь через Whisper.
    Возвращает текст сказанного или пустую строку.
    """
    audio_path = extract_audio_from_video(video_path)
    if not audio_path:
        return ""
    try:
        from src.communication.whisper_stt import transcribe_audio
        text = transcribe_audio(audio_path)
        if not text or text.startswith("("):
            return ""
        return text.strip()
    finally:
        if audio_path.exists():
            try:
                audio_path.unlink(missing_ok=True)
            except Exception:
                pass


def describe_video_frame(video_path: str | Path) -> str:
    """
    Извлечь кадр из видео и получить краткое текстовое описание через OpenAI vision.
    Возвращает описание или пустую строку при ошибке.
    """
    frame_path = _extract_frame(video_path)
    if not frame_path or not frame_path.exists():
        return ""
    try:
        data = frame_path.read_bytes()
        frame_path.unlink(missing_ok=True)
        b64 = base64.standard_b64encode(data).decode("ascii")
    except Exception:
        if frame_path and frame_path.exists():
            try:
                frame_path.unlink(missing_ok=True)
            except Exception:
                pass
        return ""
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return ""
        client = OpenAI(api_key=key)
        content = [
            {"type": "text", "text": "Опиши кратко, что на этом кадре видео (1–2 предложения): люди, место, действие, объекты."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": content}],
            max_tokens=150,
        )
        if not r.choices or not r.choices[0].message.content:
            return ""
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return ""


def describe_image(image_path: str | Path) -> str:
    """
    Получить краткое текстовое описание картинки через OpenAI vision.
    image_path — путь к файлу (JPEG, PNG и т.д.). Возвращает описание или пустую строку.
    """
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        data = path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
    except Exception:
        return ""
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png" if suffix == ".png" else "image/jpeg"
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return ""
        client = OpenAI(api_key=key)
        content = [
            {"type": "text", "text": "Опиши кратко, что на изображении (1–2 предложения): объекты, сцена, текст если есть."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": content}],
            max_tokens=150,
        )
        if not r.choices or not r.choices[0].message.content:
            return ""
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return ""
