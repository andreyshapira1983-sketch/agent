"""
VideoEditTool — редактирование видео (замена Adobe Premiere / After Effects).

Бесплатные движки:
  1. ffmpeg — основной (бесплатно, https://ffmpeg.org/download.html)
       Умеет: cut, concat, resize, speed, audio, subtitles, GIF, WebM, сжатие.
       Windows: winget install Gyan.FFmpeg
  2. Adobe After Effects ExtendScript (.jsx) — если AE установлен (платный Adobe CC)
  3. Adobe Premiere Pro ExtendScript (.jsx) — если PP установлен (платный Adobe CC)

Переменные окружения: не нужны (ffmpeg бесплатен).
AE_PATH / PP_PATH — если AfterFX.exe / Premiere не в PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from tools.tool_layer import BaseTool


def _ff() -> str | None:
    return shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')


def _ff_probe() -> str | None:
    return shutil.which('ffprobe') or shutil.which('ffprobe.exe')


def _no_ff() -> dict:
    return {
        'ok': False,
        'error': 'ffmpeg не найден.',
        'install': (
            'Скачай бесплатно: https://ffmpeg.org/download.html\n'
            'Windows: winget install Gyan.FFmpeg'
        ),
    }


def _run(args: list, timeout: int = 300) -> dict:
    exe = _ff()
    if not exe:
        return _no_ff()
    try:
        r = subprocess.run(
            [exe] + args,
            capture_output=True, text=True, errors='replace', timeout=timeout,
        )
        return {
            'ok': r.returncode == 0,
            'stdout': r.stdout[-2000:],
            'stderr': r.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'Таймаут {timeout}с превышен'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


class VideoEditTool(BaseTool):
    name = 'video_edit'
    description = (
        'Редактирование видео через ffmpeg (бесплатно): обрезка, склейка, изменение размера, '
        'скорость, добавление/удаление аудио, субтитры, извлечение кадров, '
        'GIF, WebM, сжатие H.264. Adobe After Effects / Premiere через ExtendScript '
        '(если Adobe CC установлен).'
    )

    def __init__(self):
        super().__init__(self.__class__.name, self.__class__.description)

    # ─── Информация ──────────────────────────────────────────────────────────

    def info(self, src: str) -> dict:
        """Информация о видеофайле (длительность, кодек, разрешение, FPS)."""
        probe = _ff_probe()
        if not probe:
            return {'ok': False, 'error': 'ffprobe не найден (установи ffmpeg)'}
        import json as _json
        try:
            r = subprocess.run(
                [probe, '-v', 'quiet', '-print_format', 'json',
                 '-show_streams', '-show_format', src],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                return {'ok': True, **_json.loads(r.stdout)}
            return {'ok': False, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── Основные операции ───────────────────────────────────────────────────

    def cut(self, src: str, dst: str, start: str, end: str) -> dict:
        """
        Обрезать видео. start/end: '00:01:30' или секунды '90'.
        Пример: cut('in.mp4', 'out.mp4', '00:00:10', '00:01:30')
        """
        return _run(['-i', src, '-ss', str(start), '-to', str(end),
                     '-c', 'copy', '-y', dst])

    def concat(self, inputs: list, dst: str) -> dict:
        """Склеить несколько видео в одно (одинаковый кодек и разрешение)."""
        list_file = dst + '_concat.txt'
        try:
            with open(list_file, 'w', encoding='utf-8') as f:
                for p in inputs:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            return _run(['-f', 'concat', '-safe', '0', '-i', list_file,
                         '-c', 'copy', '-y', dst])
        finally:
            if os.path.exists(list_file):
                os.remove(list_file)

    def resize(self, src: str, dst: str, width: int, height: int) -> dict:
        """Изменить разрешение. Пример: 1920x1080 → 1280x720."""
        return _run(['-i', src, '-vf', f'scale={width}:{height}',
                     '-c:a', 'copy', '-y', dst])

    def change_speed(self, src: str, dst: str, speed: float) -> dict:
        """
        Изменить скорость (видео + аудио).
        speed=2.0 — ускорить в 2 раза, speed=0.5 — замедлить.
        """
        atempo = min(2.0, max(0.5, speed))
        return _run(['-i', src,
                     '-vf', f'setpts={1 / speed:.4f}*PTS',
                     '-af', f'atempo={atempo}',
                     '-y', dst])

    def add_audio(self, video: str, audio: str, dst: str,
                  replace: bool = True) -> dict:
        """Добавить или заменить аудиодорожку."""
        if replace:
            return _run(['-i', video, '-i', audio,
                         '-c:v', 'copy', '-map', '0:v:0', '-map', '1:a:0',
                         '-shortest', '-y', dst])
        return _run(['-i', video, '-i', audio,
                     '-filter_complex', 'amix=inputs=2:duration=first',
                     '-c:v', 'copy', '-y', dst])

    def mute(self, src: str, dst: str) -> dict:
        """Удалить аудиодорожку из видео."""
        return _run(['-i', src, '-c:v', 'copy', '-an', '-y', dst])

    def extract_audio(self, src: str, dst: str) -> dict:
        """Извлечь аудио из видео (расширение .mp3 / .wav / .aac)."""
        return _run(['-i', src, '-vn', '-y', dst])

    def extract_frames(self, src: str, dst_dir: str,
                       fps: float = 1.0, fmt: str = 'png') -> dict:
        """Извлечь кадры из видео. fps=1 — один кадр в секунду."""
        os.makedirs(dst_dir, exist_ok=True)
        pattern = os.path.join(dst_dir, f'frame_%04d.{fmt}')
        return _run(['-i', src, '-vf', f'fps={fps}', '-y', pattern])

    def screenshot(self, src: str, dst: str, time: str = '00:00:01') -> dict:
        """Скриншот одного кадра из видео."""
        return _run(['-ss', str(time), '-i', src, '-frames:v', '1', '-y', dst])

    # ─── Продвинутые ─────────────────────────────────────────────────────────

    def to_gif(self, src: str, dst: str,
               start: str = '0', duration: str = '5',
               fps: int = 10, width: int = 480) -> dict:
        """Конвертировать фрагмент видео в GIF."""
        palette = dst + '_pal.png'
        try:
            r1 = _run(['-ss', str(start), '-t', str(duration), '-i', src,
                       '-vf', f'fps={fps},scale={width}:-1:flags=lanczos,palettegen',
                       '-y', palette])
            if not r1['ok']:
                return r1
            return _run(['-ss', str(start), '-t', str(duration), '-i', src,
                         '-i', palette,
                         '-vf', f'fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse',
                         '-y', dst])
        finally:
            if os.path.exists(palette):
                os.remove(palette)

    def compress(self, src: str, dst: str,
                 crf: int = 28, preset: str = 'medium') -> dict:
        """
        Сжать видео H.264.
        crf: 18=отличное качество, 28=среднее, 51=минимальное.
        preset: ultrafast | fast | medium | slow | veryslow
        """
        return _run(['-i', src, '-c:v', 'libx264', '-crf', str(crf),
                     '-preset', preset, '-c:a', 'aac', '-y', dst])

    def add_subtitles(self, src: str, dst: str, srt_path: str) -> dict:
        """Вжечь субтитры из .srt файла в видео."""
        srt = os.path.abspath(srt_path).replace('\\', '/').replace(':', '\\:')
        return _run(['-i', src, '-vf', f"subtitles='{srt}'",
                     '-c:a', 'copy', '-y', dst])

    def to_webm(self, src: str, dst: str, crf: int = 33) -> dict:
        """Конвертировать в WebM VP9 (для веба)."""
        return _run(['-i', src, '-c:v', 'libvpx-vp9',
                     '-crf', str(crf), '-b:v', '0',
                     '-c:a', 'libopus', '-y', dst])

    def ffmpeg_raw(self, args: list, timeout: int = 300) -> dict:
        """Произвольная команда ffmpeg (args без 'ffmpeg' в начале)."""
        return _run(args, timeout=timeout)

    # ─── Adobe After Effects / Premiere (если установлены) ──────────────────

    def ae_run_script(self, jsx_path: str, ae_exe: str | None = None) -> dict:
        """
        Запустить .jsx ExtendScript в Adobe After Effects.
        ae_exe — путь к AfterFX.exe (опционально, иначе ищем автоматически).
        After Effects — ПЛАТНЫЙ (Adobe Creative Cloud).
        """
        exe = ae_exe or shutil.which('AfterFX') or shutil.which('AfterFX.exe')
        if not exe:
            for candidate in [
                r'C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe',
                r'C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe',
            ]:
                if os.path.exists(candidate):
                    exe = candidate
                    break
        if not exe:
            return {
                'ok': False,
                'error': 'Adobe After Effects не найден (платная программа Adobe CC).',
                'alternative': 'Используй ffmpeg_raw() — бесплатная альтернатива для большинства задач.',
            }
        try:
            r = subprocess.run([exe, '-r', jsx_path],
                               capture_output=True, text=True, timeout=300)
            return {'ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def premiere_run_script(self, jsx_path: str, pr_exe: str | None = None) -> dict:
        """
        Запустить .jsx ExtendScript в Adobe Premiere Pro.
        Premiere Pro — ПЛАТНЫЙ (Adobe Creative Cloud).
        """
        exe = pr_exe
        if not exe:
            for candidate in [
                r'C:\Program Files\Adobe\Adobe Premiere Pro 2025\Adobe Premiere Pro.exe',
                r'C:\Program Files\Adobe\Adobe Premiere Pro 2024\Adobe Premiere Pro.exe',
            ]:
                if os.path.exists(candidate):
                    exe = candidate
                    break
        if not exe:
            return {
                'ok': False,
                'error': 'Adobe Premiere Pro не найден (платная программа Adobe CC).',
                'alternative': 'Используй VideoEditTool cut/concat/compress — бесплатно через ffmpeg.',
            }
        try:
            r = subprocess.run([exe, jsx_path], capture_output=True, text=True, timeout=300)
            return {'ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, action: str = 'info', **params) -> dict:
        """
        action:
          info | cut | concat | resize | change_speed | add_audio | mute |
          extract_audio | extract_frames | screenshot | to_gif | compress |
          add_subtitles | to_webm | ffmpeg_raw |
          ae_run_script | premiere_run_script
        """
        actions = {
            'info':                self.info,
            'cut':                 self.cut,
            'concat':              self.concat,
            'resize':              self.resize,
            'change_speed':        self.change_speed,
            'add_audio':           self.add_audio,
            'mute':                self.mute,
            'extract_audio':       self.extract_audio,
            'extract_frames':      self.extract_frames,
            'screenshot':          self.screenshot,
            'to_gif':              self.to_gif,
            'compress':            self.compress,
            'add_subtitles':       self.add_subtitles,
            'to_webm':             self.to_webm,
            'ffmpeg_raw':          self.ffmpeg_raw,
            'ae_run_script':       self.ae_run_script,
            'premiere_run_script': self.premiere_run_script,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
