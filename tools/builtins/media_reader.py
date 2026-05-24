"""
tools/builtins/media_reader.py — Чтение метаданных аудио и видео файлов

Использует mutagen (чистый Python, не нужен ffmpeg для метаданных).
Для видео дополнительно поддерживается pymediainfo если установлен.

Поддерживаемые форматы:
    Аудио: .mp3, .flac, .ogg, .m4a, .aac, .wav, .wma, .opus, .aiff
    Видео: .mp4, .mkv, .avi, .mov, .wmv, .webm, .m4v, .flv

Actions:
    get_metadata   — длительность, битрейт, частота, теги (автор, альбом, год…)
    list_tags      — все теги как словарь (ID3/Vorbis/MP4)
    describe       — человекочитаемое резюме файла
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_AUDIO_EXT = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".wma", ".opus", ".aiff", ".aif"}
_VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".ts", ".mts"}
_ALL_EXT = _AUDIO_EXT | _VIDEO_EXT

_MAX_FILE_SIZE_MB = 500  # видео могут быть большими, но метаданные читаем быстро


class MediaReaderTool(ToolBase):
    """
    Читает метаданные аудио и видео файлов (без декодирования!).

    params:
        file_path  (str): Путь к медиа-файлу
        action     (str, optional): get_metadata | list_tags | describe
                                    (по умолчанию: get_metadata)
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="media_reader",
            description=(
                "Читает метаданные аудио (.mp3, .flac, .ogg, .m4a, .wav…) "
                "и видео (.mp4, .mkv, .avi, .mov…) файлов. "
                "Не декодирует — только метаданные (теги, длина, битрейт)."
            ),
            parameters={
                "file_path": "str — путь к аудио или видео файлу",
                "action":    "str (optional) — get_metadata | list_tags | describe",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        t0 = time.perf_counter()
        try:
            import mutagen  # noqa: F401
        except ImportError:
            return self._fail(
                "mutagen не установлен. Запустите: pip install mutagen"
            )

        file_path = params.get("file_path", "")
        if not file_path:
            return self._fail("Параметр 'file_path' обязателен")

        path = Path(str(file_path))
        if not path.exists():
            return self._fail(f"Файл не найден: {file_path}")
        if path.suffix.lower() not in _ALL_EXT:
            return self._fail(
                f"Неподдерживаемый формат: {path.suffix!r}. "
                f"Аудио: {', '.join(sorted(_AUDIO_EXT))}. "
                f"Видео: {', '.join(sorted(_VIDEO_EXT))}"
            )

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > _MAX_FILE_SIZE_MB:
            return self._fail(
                f"Файл слишком большой: {size_mb:.1f} МБ (лимит {_MAX_FILE_SIZE_MB} МБ)"
            )

        action = str(params.get("action", "get_metadata")).lower()
        if action not in ("get_metadata", "list_tags", "describe"):
            return self._fail(
                f"Неизвестное действие: {action!r}. "
                "Доступно: get_metadata | list_tags | describe"
            )

        try:
            import mutagen
            audio = mutagen.File(str(path), easy=True)
            audio_raw = mutagen.File(str(path), easy=False)
            dur_ms = (time.perf_counter() - t0) * 1000

            media_type = "video" if path.suffix.lower() in _VIDEO_EXT else "audio"

            if action == "get_metadata":
                return self._get_metadata(audio, path, size_mb, media_type, dur_ms)
            elif action == "list_tags":
                return self._list_tags(audio_raw, path, dur_ms)
            else:
                return self._describe(audio, path, size_mb, media_type, dur_ms)

        except Exception as exc:
            return self._fail(f"Ошибка чтения медиа-файла: {exc}")

    # ------------------------------------------------------------------
    def _get_metadata(
        self,
        audio: Any,
        path: Path,
        size_mb: float,
        media_type: str,
        dur_ms: float,
    ) -> ToolResult:
        info: dict[str, Any] = {
            "file":       path.name,
            "type":       media_type,
            "format":     path.suffix.lower().lstrip(".").upper(),
            "file_size_mb": round(size_mb, 2),
        }

        if audio is not None:
            # Длительность
            if hasattr(audio, "info") and audio.info is not None:
                raw_dur = getattr(audio.info, "length", None)
                if raw_dur is not None:
                    total_sec = int(raw_dur)
                    h, rem = divmod(total_sec, 3600)
                    m, s = divmod(rem, 60)
                    info["duration_sec"] = round(raw_dur, 1)
                    info["duration_str"] = (
                        f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                    )

                bitrate = getattr(audio.info, "bitrate", None)
                if bitrate:
                    info["bitrate_kbps"] = round(bitrate / 1000, 1)

                sample_rate = getattr(audio.info, "sample_rate", None)
                if sample_rate:
                    info["sample_rate_hz"] = sample_rate

                channels = getattr(audio.info, "channels", None)
                if channels is not None:
                    info["channels"] = channels
                    info["channels_str"] = {1: "Mono", 2: "Stereo"}.get(channels, str(channels))

            # Теги (easy mode)
            if audio:
                for key in ("title", "artist", "album", "date", "genre",
                            "tracknumber", "albumartist", "composer", "comment"):
                    val = audio.get(key)
                    if val:
                        info[key] = val[0] if len(val) == 1 else list(val)

        return self._ok(info, duration_ms=round(dur_ms, 2))

    # ------------------------------------------------------------------
    def _list_tags(self, audio_raw: Any, path: Path, dur_ms: float) -> ToolResult:
        if audio_raw is None or not audio_raw.tags:
            return self._ok(
                {},
                note="Теги отсутствуют в файле",
                duration_ms=round(dur_ms, 2),
            )

        tags: dict[str, Any] = {}
        for key, value in audio_raw.tags.items():
            # ID3 frames имеют атрибут text или toString()
            try:
                if hasattr(value, "text"):
                    tags[str(key)] = list(value.text)
                elif hasattr(value, "value"):
                    tags[str(key)] = str(value.value)
                else:
                    v = str(value)
                    # Ограничиваем длинные значения (например, cover art)
                    tags[str(key)] = v[:500] + "…" if len(v) > 500 else v
            except Exception:
                tags[str(key)] = "<не читается>"

        return self._ok(tags, duration_ms=round(dur_ms, 2), tag_count=len(tags))

    # ------------------------------------------------------------------
    def _describe(
        self,
        audio: Any,
        path: Path,
        size_mb: float,
        media_type: str,
        dur_ms: float,
    ) -> ToolResult:
        parts: list[str] = []

        fmt = path.suffix.lower().lstrip(".").upper()
        parts.append(f"Файл: {path.name} ({fmt}, {size_mb:.1f} МБ)")
        parts.append(f"Тип: {'Видео' if media_type == 'video' else 'Аудио'}")

        if audio is not None and hasattr(audio, "info") and audio.info is not None:
            raw_dur = getattr(audio.info, "length", None)
            if raw_dur is not None:
                total_sec = int(raw_dur)
                m, s = divmod(total_sec % 3600, 60)
                h = total_sec // 3600
                dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                parts.append(f"Длительность: {dur_str}")

            bitrate = getattr(audio.info, "bitrate", None)
            if bitrate:
                parts.append(f"Битрейт: {bitrate // 1000} кбит/с")

            sample_rate = getattr(audio.info, "sample_rate", None)
            if sample_rate:
                parts.append(f"Частота дискретизации: {sample_rate} Гц")

            channels = getattr(audio.info, "channels", None)
            if channels is not None:
                ch_str = {1: "Моно", 2: "Стерео"}.get(channels, f"{channels} каналов")
                parts.append(f"Каналы: {ch_str}")

        if audio:
            title = audio.get("title", [None])[0] if audio.get("title") else None
            artist = audio.get("artist", [None])[0] if audio.get("artist") else None
            album = audio.get("album", [None])[0] if audio.get("album") else None
            year = audio.get("date", [None])[0] if audio.get("date") else None

            if title:
                parts.append(f"Название: {title}")
            if artist:
                parts.append(f"Исполнитель: {artist}")
            if album:
                parts.append(f"Альбом: {album}")
            if year:
                parts.append(f"Год: {year}")

        description = "\n".join(parts)
        return self._ok(
            description,
            duration_ms=round(dur_ms, 2),
            file=path.name,
            media_type=media_type,
        )
