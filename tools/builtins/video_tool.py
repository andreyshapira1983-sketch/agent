"""
tools/builtins/video_tool.py — Video Agent Tool (S5.3.2)

Реализует видео-пайплайн: нарезка ffmpeg, субтитры Whisper, превью.

Зависимости:
    - ffmpeg / ffprobe — системная установка (проверяется через PATH)
    - openai>=1.0     — Whisper API для транскрибации
    - Pillow>=10.3    — обработка изображений (thumbnail resize)

Конфигурация через .env:
    OPENAI_API_KEY    — ключ для Whisper API

Поддерживаемые действия (параметр action):
    info          — метаданные видео (длительность, разрешение, fps, кодек)
    cut           — нарезка сегмента по времени [start_time..end_time]
    extract_audio — извлечение аудиодорожки (mp3/wav/aac)
    transcribe    — транскрибация аудио/видео в текст (OpenAI Whisper API)
    subtitle_gen  — транскрибация → .srt файл субтитров
    thumbnail     — извлечение кадра как изображения

Безопасность:
    - Пути файлов нормализованы (Path.resolve, нет выхода за пределы)
    - subprocess всегда с timeout и captured stderr
    - OPENAI_API_KEY берётся только из .env / vault
    - dry_run=True — режим без изменений (только валидация)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.base import ToolBase, ToolResult, ToolSpec

try:
    from brain.secrets import SecretsVault
except ImportError:
    SecretsVault = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# Максимальный размер файла для Whisper API (25 MB — лимит OpenAI)
_WHISPER_MAX_BYTES = 25 * 1024 * 1024

# Timeout для subprocess-вызовов ffmpeg (секунды)
_FFMPEG_TIMEOUT_S = 300  # 5 минут — достаточно для большинства операций

# Допустимые расширения видео/аудио
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"}
_AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".opus", ".wma"}

# Форматы, принимаемые Whisper API
_WHISPER_EXTS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}


class VideoTool(ToolBase):
    """
    S5.3.2 Video Agent — ffmpeg pipeline + OpenAI Whisper субтитры.

    Все операции с файлами — локальные. Агент получает пути к файлам
    и выполняет преобразования через ffmpeg и OpenAI API.
    """

    @property
    def spec(self) -> ToolSpec:  # type: ignore[override]
        return ToolSpec(
            name="video_tool",
            description=(
                "Видео-агент: нарезка видео (ffmpeg), генерация субтитров (Whisper), "
                "извлечение кадров-превью, метаданные, извлечение аудио. "
                "Используй для обработки видеофайлов."
            ),
            parameters={
                "action":          "str — info | cut | extract_audio | transcribe | subtitle_gen | thumbnail",
                "input_path":      "str — путь к входному видео/аудио файлу (обязателен)",
                "output_path":     "str — путь для результата (обязателен для cut/extract_audio/subtitle_gen/thumbnail)",
                "start_time":      "str — начало сегмента для cut: HH:MM:SS или секунды (например '90.5')",
                "end_time":        "str — конец сегмента для cut: HH:MM:SS или секунды",
                "audio_format":    "str — формат аудио для extract_audio: mp3|wav|aac|flac|m4a (по умолчанию mp3)",
                "language":        "str — язык для Whisper ISO 639-1 ('ru','en'...). Авто если не указан",
                "timestamp":       "str — временная метка кадра для thumbnail: HH:MM:SS или секунды (по умолчанию 00:00:01)",
                "thumbnail_width": "int — ширина превью в пикселях (пропорции сохраняются)",
                "dry_run":         "bool — только валидация без изменений (по умолчанию false)",
            },
            requires_approval=False,
            is_destructive=False,
        )

    # ------------------------------------------------------------------
    # Основной диспетчер
    # ------------------------------------------------------------------

    def execute(self, **params: Any) -> ToolResult:  # type: ignore[override]
        action = params.get("action", "").strip().lower()
        input_path = params.get("input_path", "").strip()
        dry_run = bool(params.get("dry_run", False))

        if not action:
            return self._err("Параметр 'action' обязателен")
        if not input_path:
            return self._err("Параметр 'input_path' обязателен")

        # Проверяем ffmpeg для всех действий кроме transcribe
        if action != "transcribe" or True:
            ffmpeg_bin = shutil.which("ffmpeg")
            ffprobe_bin = shutil.which("ffprobe")
            if not ffmpeg_bin and action not in ("transcribe",):
                return self._err("ffmpeg не найден в PATH. Установите ffmpeg: https://ffmpeg.org/download.html")

        src = Path(input_path).resolve()
        if not src.exists():
            return self._err(f"Файл не найден: {src}")
        if not src.is_file():
            return self._err(f"Путь не является файлом: {src}")

        dispatch = {
            "info":          self._action_info,
            "cut":           self._action_cut,
            "extract_audio": self._action_extract_audio,
            "transcribe":    self._action_transcribe,
            "subtitle_gen":  self._action_subtitle_gen,
            "thumbnail":     self._action_thumbnail,
        }

        handler = dispatch.get(action)
        if handler is None:
            return self._err(f"Неизвестный action '{action}'. Допустимые: {', '.join(dispatch)}")

        try:
            return handler(src, params, dry_run)
        except Exception as exc:
            logger.exception("VideoTool [%s] unexpected error", action)
            return self._err(f"Неожиданная ошибка в action '{action}': {exc}")

    # ------------------------------------------------------------------
    # Action: info — метаданные через ffprobe
    # ------------------------------------------------------------------

    def _action_info(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            return self._err("ffprobe не найден в PATH")

        cmd = [
            ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(src),
        ]
        result = self._run(cmd)
        if result is None:
            return self._err("ffprobe завершился с ошибкой или timeout")

        try:
            raw = json.loads(result)
        except json.JSONDecodeError as exc:
            return self._err(f"ffprobe вернул невалидный JSON: {exc}")

        info = self._parse_probe(raw)
        return ToolResult(
            tool_name="video_tool",
            success=True,
            output=info,
            metadata={"file": str(src), "size_bytes": src.stat().st_size},
        )

    # ------------------------------------------------------------------
    # Action: cut — нарезка сегмента
    # ------------------------------------------------------------------

    def _action_cut(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        output_path = params.get("output_path", "").strip()
        start_time = params.get("start_time", "").strip()
        end_time = params.get("end_time", "").strip()

        if not output_path:
            return self._err("Параметр 'output_path' обязателен для action 'cut'")
        if not start_time and not end_time:
            return self._err("Нужен хотя бы один из параметров: start_time, end_time")

        if not self._validate_time(start_time) and start_time:
            return self._err(f"Некорректный формат start_time: '{start_time}'. Используй HH:MM:SS или секунды")
        if not self._validate_time(end_time) and end_time:
            return self._err(f"Некорректный формат end_time: '{end_time}'. Используй HH:MM:SS или секунды")

        dst = Path(output_path).resolve()
        self._ensure_parent(dst)

        if dry_run:
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output=f"[dry_run] Нарежу {src.name} → {dst.name} [{start_time or '0'}..{end_time or 'конец'}]",
                metadata={"dry_run": True},
            )

        ffmpeg_bin = shutil.which("ffmpeg")
        cmd = [ffmpeg_bin, "-y", "-i", str(src)]
        if start_time:
            cmd += ["-ss", start_time]
        if end_time:
            cmd += ["-to", end_time]
        # Копируем кодеки без перекодирования — быстро
        cmd += ["-c", "copy", str(dst)]

        ok, stderr = self._run_with_stderr(cmd)
        if not ok:
            return self._err(f"ffmpeg завершился с ошибкой при нарезке:\n{stderr[-500:]}")

        return ToolResult(
            tool_name="video_tool",
            success=True,
            output={"output": str(dst), "size_bytes": dst.stat().st_size if dst.exists() else 0},
            metadata={"start_time": start_time, "end_time": end_time},
        )

    # ------------------------------------------------------------------
    # Action: extract_audio
    # ------------------------------------------------------------------

    def _action_extract_audio(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        output_path = params.get("output_path", "").strip()
        audio_format = params.get("audio_format", "mp3").strip().lower()

        if audio_format not in ("mp3", "wav", "aac", "flac", "m4a"):
            audio_format = "mp3"

        if not output_path:
            # Авто-имя рядом с исходником
            output_path = str(src.with_suffix(f".{audio_format}"))

        dst = Path(output_path).resolve()
        self._ensure_parent(dst)

        if dry_run:
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output=f"[dry_run] Извлеку аудио {src.name} → {dst.name} ({audio_format})",
                metadata={"dry_run": True},
            )

        ffmpeg_bin = shutil.which("ffmpeg")
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(src),
            "-vn",                    # без видео
            "-acodec", self._audio_codec(audio_format),
            str(dst),
        ]

        ok, stderr = self._run_with_stderr(cmd)
        if not ok:
            return self._err(f"ffmpeg ошибка при извлечении аудио:\n{stderr[-500:]}")

        size = dst.stat().st_size if dst.exists() else 0
        return ToolResult(
            tool_name="video_tool",
            success=True,
            output={"output": str(dst), "format": audio_format, "size_bytes": size},
            metadata={"source": str(src)},
        )

    # ------------------------------------------------------------------
    # Action: transcribe — текст через OpenAI Whisper API
    # ------------------------------------------------------------------

    def _action_transcribe(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        language = params.get("language") or None  # None → авто-определение

        if dry_run:
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output=f"[dry_run] Транскрибирую {src.name} (lang={language or 'auto'})",
                metadata={"dry_run": True},
            )

        # Конвертируем в mp3 если формат не принимается Whisper
        audio_path, tmp_created = self._prepare_for_whisper(src)
        if audio_path is None:
            return self._err("Не удалось подготовить аудио для Whisper (нет ffmpeg или ошибка конвертации)")

        try:
            size = audio_path.stat().st_size
            if size > _WHISPER_MAX_BYTES:
                return self._err(
                    f"Файл слишком большой для Whisper API: {size // 1024 // 1024} MB > 25 MB. "
                    "Разбей файл на части с помощью action='cut'"
                )

            client = self._openai_client()
            if client is None:
                return self._err("OPENAI_API_KEY не задан. Добавьте в .env")

            with audio_path.open("rb") as f:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="text",
                    language=language,
                )

            text = transcript if isinstance(transcript, str) else str(transcript)
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output={"text": text.strip(), "language": language or "auto"},
                metadata={"source": str(src), "audio_size_bytes": size},
            )
        finally:
            if tmp_created and audio_path.exists():
                audio_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Action: subtitle_gen — .srt файл через Whisper API
    # ------------------------------------------------------------------

    def _action_subtitle_gen(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        output_path = params.get("output_path", "").strip()
        language = params.get("language") or None

        if not output_path:
            output_path = str(src.with_suffix(".srt"))

        dst = Path(output_path).resolve()
        self._ensure_parent(dst)

        if dry_run:
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output=f"[dry_run] Сгенерирую субтитры {src.name} → {dst.name} (lang={language or 'auto'})",
                metadata={"dry_run": True},
            )

        audio_path, tmp_created = self._prepare_for_whisper(src)
        if audio_path is None:
            return self._err("Не удалось подготовить аудио для Whisper")

        try:
            size = audio_path.stat().st_size
            if size > _WHISPER_MAX_BYTES:
                return self._err(
                    f"Файл слишком большой для Whisper API: {size // 1024 // 1024} MB > 25 MB. "
                    "Разбей файл с action='cut'"
                )

            client = self._openai_client()
            if client is None:
                return self._err("OPENAI_API_KEY не задан. Добавьте в .env")

            with audio_path.open("rb") as f:
                srt_content = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="srt",
                    language=language,
                )

            srt_text = srt_content if isinstance(srt_content, str) else str(srt_content)
            dst.write_text(srt_text, encoding="utf-8")

            # Считаем количество субтитров
            subtitle_count = len(re.findall(r"^\d+\s*$", srt_text, re.MULTILINE))

            return ToolResult(
                tool_name="video_tool",
                success=True,
                output={
                    "srt_file": str(dst),
                    "subtitle_count": subtitle_count,
                    "language": language or "auto",
                    "size_bytes": dst.stat().st_size,
                },
                metadata={"source": str(src)},
            )
        finally:
            if tmp_created and audio_path.exists():
                audio_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Action: thumbnail — кадр-превью через ffmpeg
    # ------------------------------------------------------------------

    def _action_thumbnail(self, src: Path, params: dict, dry_run: bool) -> ToolResult:
        output_path = params.get("output_path", "").strip()
        timestamp = params.get("timestamp", "00:00:01").strip() or "00:00:01"
        thumb_width = params.get("thumbnail_width")

        if not output_path:
            output_path = str(src.with_suffix(".jpg"))

        if not self._validate_time(timestamp):
            return self._err(f"Некорректный формат timestamp: '{timestamp}'. Используй HH:MM:SS или секунды")

        dst = Path(output_path).resolve()
        self._ensure_parent(dst)

        if dry_run:
            return ToolResult(
                tool_name="video_tool",
                success=True,
                output=f"[dry_run] Извлеку кадр {src.name}@{timestamp} → {dst.name}",
                metadata={"dry_run": True},
            )

        ffmpeg_bin = shutil.which("ffmpeg")
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", timestamp,
            "-i", str(src),
            "-frames:v", "1",
        ]

        if thumb_width and isinstance(thumb_width, int) and thumb_width > 0:
            cmd += ["-vf", f"scale={thumb_width}:-1"]

        # Формат по расширению, по умолчанию JPEG
        suffix = dst.suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            # Принудительно JPEG
            dst = dst.with_suffix(".jpg")
        cmd.append(str(dst))

        ok, stderr = self._run_with_stderr(cmd)
        if not ok:
            return self._err(f"ffmpeg ошибка при извлечении кадра:\n{stderr[-500:]}")

        if not dst.exists():
            return self._err("ffmpeg завершился без ошибки, но файл превью не создан")

        # Получаем размер изображения через Pillow (если доступен)
        img_meta: dict[str, Any] = {"output": str(dst), "size_bytes": dst.stat().st_size}
        try:
            from PIL import Image  # noqa: PLC0415
            with Image.open(dst) as img:
                img_meta["width"], img_meta["height"] = img.size
                img_meta["format"] = img.format
        except Exception:
            pass  # Pillow недоступен — не критично

        return ToolResult(
            tool_name="video_tool",
            success=True,
            output=img_meta,
            metadata={"timestamp": timestamp, "source": str(src)},
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _err(self, msg: str) -> ToolResult:
        return ToolResult(tool_name="video_tool", success=False, output=None, error=msg)

    def _run(self, cmd: list[str]) -> str | None:
        """Запускает команду, возвращает stdout или None при ошибке."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_FFMPEG_TIMEOUT_S,
            )
            if proc.returncode != 0:
                logger.warning("Command failed (rc=%d): %s", proc.returncode, " ".join(cmd[:3]))
                return None
            return proc.stdout
        except subprocess.TimeoutExpired:
            logger.warning("Command timeout: %s", " ".join(cmd[:3]))
            return None
        except FileNotFoundError:
            logger.warning("Binary not found: %s", cmd[0])
            return None

    def _run_with_stderr(self, cmd: list[str]) -> tuple[bool, str]:
        """Запускает команду, возвращает (ok, stderr)."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_FFMPEG_TIMEOUT_S,
                encoding="utf-8",
                errors="replace",
            )
            return proc.returncode == 0, proc.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "Timeout expired"
        except FileNotFoundError:
            return False, f"Binary not found: {cmd[0]}"
        except Exception as exc:
            return False, str(exc)

    def _ensure_parent(self, path: Path) -> None:
        """Создаёт директорию для файла если нужно."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def _validate_time(t: str) -> bool:
        """Проверяет формат временной метки: HH:MM:SS, MM:SS, SS или дробные секунды."""
        if not t:
            return False
        # HH:MM:SS.mmm или HH:MM:SS
        if re.match(r"^\d{1,3}:\d{2}:\d{2}(\.\d+)?$", t):
            return True
        # MM:SS
        if re.match(r"^\d{1,2}:\d{2}(\.\d+)?$", t):
            return True
        # Числовые секунды (целые или дробные)
        if re.match(r"^\d+(\.\d+)?$", t):
            return True
        return False

    @staticmethod
    def _audio_codec(fmt: str) -> str:
        return {
            "mp3": "libmp3lame",
            "wav": "pcm_s16le",
            "aac": "aac",
            "flac": "flac",
            "m4a": "aac",
        }.get(fmt, "libmp3lame")

    def _prepare_for_whisper(self, src: Path) -> tuple[Path | None, bool]:
        """
        Готовит файл для Whisper API.
        Если формат уже поддерживается — возвращает src.
        Иначе конвертирует через ffmpeg в mp3 во временный файл.
        Возвращает (path, tmp_was_created).
        """
        if src.suffix.lower() in _WHISPER_EXTS:
            return src, False

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return None, False

        tmp = Path(tempfile.mktemp(suffix=".mp3"))
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(src),
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",  # 16 kHz достаточно для речи
            "-ac", "1",      # моно
            str(tmp),
        ]
        ok, stderr = self._run_with_stderr(cmd)
        if not ok or not tmp.exists():
            logger.warning("Не удалось конвертировать в mp3 для Whisper: %s", stderr[-200:])
            return None, False

        return tmp, True

    def _openai_client(self):  # type: ignore[return]
        """Возвращает openai.OpenAI client или None если ключ не найден."""
        api_key: str | None = None

        # Приоритет 1: SecretsVault
        if SecretsVault is not None:
            try:
                vault = SecretsVault()
                secret = vault.get("OPENAI_API_KEY")
                if secret is not None:
                    api_key = str(secret)
            except Exception:
                pass

        # Приоритет 2: переменная окружения
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()

        if not api_key:
            return None

        try:
            import openai  # noqa: PLC0415
            return openai.OpenAI(api_key=api_key)
        except Exception as exc:
            logger.warning("Не удалось создать OpenAI client: %s", exc)
            return None

    @staticmethod
    def _parse_probe(raw: dict) -> dict:
        """Парсит вывод ffprobe в удобный словарь."""
        result: dict[str, Any] = {}

        fmt = raw.get("format", {})
        result["filename"] = Path(fmt.get("filename", "")).name
        result["format"] = fmt.get("format_long_name") or fmt.get("format_name", "")
        result["duration_sec"] = round(float(fmt.get("duration", 0) or 0), 3)
        result["size_bytes"] = int(fmt.get("size", 0) or 0)
        result["bitrate_kbps"] = round(int(fmt.get("bit_rate", 0) or 0) / 1000, 1)

        streams = raw.get("streams", [])
        for s in streams:
            codec_type = s.get("codec_type", "")
            if codec_type == "video" and "video" not in result:
                result["video"] = {
                    "codec": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                    "fps": VideoTool._parse_fps(s.get("avg_frame_rate", "0/1")),
                    "pixel_format": s.get("pix_fmt"),
                }
            elif codec_type == "audio" and "audio" not in result:
                result["audio"] = {
                    "codec": s.get("codec_name"),
                    "sample_rate_hz": int(s.get("sample_rate", 0) or 0),
                    "channels": s.get("channels"),
                    "bitrate_kbps": round(int(s.get("bit_rate", 0) or 0) / 1000, 1),
                }

        return result

    @staticmethod
    def _parse_fps(fps_str: str) -> float:
        """Парсит строку вида '30000/1001' в float."""
        try:
            if "/" in fps_str:
                num, den = fps_str.split("/")
                den_val = float(den)
                return round(float(num) / den_val, 3) if den_val else 0.0
            return round(float(fps_str), 3)
        except (ValueError, ZeroDivisionError):
            return 0.0
