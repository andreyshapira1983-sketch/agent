"""
ImageEditTool — редактирование изображений (замена Photoshop / Illustrator).

Бесплатные движки (агент использует что установлено):
  1. Pillow       pip install pillow          — основной (resize, crop, filters, text, watermark)
  2. GIMP CLI     https://www.gimp.org        — сложные batch-операции
  3. Inkscape CLI https://inkscape.org        — SVG → PNG / PDF / EPS
  4. ImageMagick  https://imagemagick.org     — batch, конвертация, сложные эффекты

Переменные окружения: не нужны (все инструменты бесплатны).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tools.tool_layer import BaseTool


def _pillow_ok() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _cmd(name: str) -> str | None:
    return shutil.which(name)


class ImageEditTool(BaseTool):
    name = 'image_edit'
    description = (
        'Редактирование изображений: resize, crop, rotate, flip, grayscale, blur, sharpen, '
        'convert format, add text/watermark, thumbnail, batch convert. '
        'SVG→PNG через Inkscape. Сложные операции через GIMP CLI / ImageMagick CLI. '
        'Всё бесплатно: Pillow + GIMP + Inkscape + ImageMagick.'
    )

    def __init__(self):
        super().__init__(self.__class__.name, self.__class__.description)

    # ─── Pillow-операции ─────────────────────────────────────────────────────

    def _pil(self, src: str, dst: str, op: str, **kw) -> dict:
        if not _pillow_ok():
            return {
                'ok': False,
                'error': 'Pillow не установлен.',
                'install': 'pip install pillow',
            }
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        try:
            img = Image.open(src)
            if op == 'resize':
                img = img.resize((int(kw['width']), int(kw['height'])), Image.LANCZOS)  # type: ignore[attr-defined]
            elif op == 'crop':
                img = img.crop((kw['left'], kw['top'], kw['right'], kw['bottom']))
            elif op == 'rotate':
                img = img.rotate(kw.get('angle', 90), expand=True)
            elif op == 'flip':
                img = img.transpose(
                    Image.Transpose.FLIP_LEFT_RIGHT
                    if kw.get('direction', 'horizontal') == 'horizontal'
                    else Image.Transpose.FLIP_TOP_BOTTOM
                )
            elif op == 'grayscale':
                img = img.convert('L').convert('RGB')
            elif op == 'blur':
                img = img.filter(ImageFilter.GaussianBlur(radius=kw.get('radius', 2)))
            elif op == 'sharpen':
                img = img.filter(ImageFilter.SHARPEN)
            elif op == 'text':
                draw = ImageDraw.Draw(img)
                fp = kw.get('font_path')
                sz = kw.get('font_size', 24)
                try:
                    font = ImageFont.truetype(fp, size=sz) if fp and os.path.exists(fp) \
                        else ImageFont.load_default(size=sz)
                except TypeError:
                    font = ImageFont.load_default()
                color = tuple(kw.get('color', [255, 255, 255]))
                draw.text((kw.get('x', 10), kw.get('y', 10)), kw.get('text', ''),
                          fill=color, font=font)
            elif op == 'watermark':
                mark = Image.open(kw['watermark_path']).convert('RGBA')
                if kw.get('wm_width') or kw.get('wm_height'):
                    mark = mark.resize((
                        int(kw.get('wm_width', mark.width)),
                        int(kw.get('wm_height', mark.height)),
                    ))
                img = img.convert('RGBA')
                img.paste(mark, (int(kw.get('x', 0)), int(kw.get('y', 0))), mark)
                img = img.convert('RGB')
            elif op == 'thumbnail':
                img.thumbnail(
                    (int(kw.get('max_width', 256)), int(kw.get('max_height', 256))),
                    Image.LANCZOS,  # type: ignore[attr-defined]
                )
            # save
            fmt = Path(dst).suffix.lstrip('.').upper() or 'PNG'
            if fmt == 'JPG':
                fmt = 'JPEG'
            img.save(dst, fmt)
            return {'ok': True, 'output': dst, 'size': list(img.size)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def info(self, src: str) -> dict:
        """Информация об изображении (размер, формат, режим)."""
        if not _pillow_ok():
            return {'ok': False, 'error': 'Pillow не установлен. pip install pillow'}
        from PIL import Image
        try:
            with Image.open(src) as img:
                return {
                    'ok': True, 'path': src,
                    'format': img.format, 'mode': img.mode,
                    'width': img.width, 'height': img.height,
                    'size': list(img.size),
                }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def resize(self, src: str, dst: str, width: int, height: int) -> dict:
        """Изменить размер изображения."""
        return self._pil(src, dst, 'resize', width=width, height=height)

    def crop(self, src: str, dst: str,
             left: int, top: int, right: int, bottom: int) -> dict:
        """Обрезать изображение."""
        return self._pil(src, dst, 'crop', left=left, top=top, right=right, bottom=bottom)

    def rotate(self, src: str, dst: str, angle: float) -> dict:
        """Повернуть изображение на угол (градусы)."""
        return self._pil(src, dst, 'rotate', angle=angle)

    def flip(self, src: str, dst: str,
             direction: str = 'horizontal') -> dict:
        """Отразить изображение. direction: horizontal | vertical."""
        return self._pil(src, dst, 'flip', direction=direction)

    def grayscale(self, src: str, dst: str) -> dict:
        """Перевести в чёрно-белый."""
        return self._pil(src, dst, 'grayscale')

    def blur(self, src: str, dst: str, radius: float = 2) -> dict:
        """Размытие Гаусса."""
        return self._pil(src, dst, 'blur', radius=radius)

    def sharpen(self, src: str, dst: str) -> dict:
        """Повышение резкости."""
        return self._pil(src, dst, 'sharpen')

    def convert(self, src: str, dst: str) -> dict:
        """Конвертировать формат (jpeg→png, png→webp и т.д.)."""
        return self._pil(src, dst, 'convert')

    def add_text(self, src: str, dst: str, text: str,
                 x: int = 10, y: int = 10,
                 font_size: int = 24,
                 color: list | None = None,
                 font_path: str | None = None) -> dict:
        """Нанести текст на изображение."""
        return self._pil(src, dst, 'text', text=text, x=x, y=y,
                         font_size=font_size, color=color or [255, 255, 255],
                         font_path=font_path)

    def watermark(self, src: str, dst: str, watermark_path: str,
                  x: int = 0, y: int = 0,
                  wm_width: int | None = None, wm_height: int | None = None) -> dict:
        """Наложить водяной знак."""
        return self._pil(src, dst, 'watermark', watermark_path=watermark_path,
                         x=x, y=y, wm_width=wm_width, wm_height=wm_height)

    def thumbnail(self, src: str, dst: str,
                  max_width: int = 256, max_height: int = 256) -> dict:
        """Создать миниатюру (thumbnail) с сохранением пропорций."""
        return self._pil(src, dst, 'thumbnail', max_width=max_width, max_height=max_height)

    def batch_convert(self, src_dir: str, dst_dir: str, fmt: str = 'png') -> dict:
        """Массовая конвертация всех изображений в папке."""
        if not _pillow_ok():
            return {'ok': False, 'error': 'Pillow не установлен. pip install pillow'}
        from PIL import Image
        os.makedirs(dst_dir, exist_ok=True)
        results = []
        for f in Path(src_dir).iterdir():
            if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp'):
                dst = os.path.join(dst_dir, f.stem + '.' + fmt.lower())
                try:
                    with Image.open(str(f)) as img:
                        sf = fmt.upper()
                        if sf == 'JPG':
                            sf = 'JPEG'
                        img.save(dst, sf)
                    results.append({'file': f.name, 'ok': True, 'output': dst})
                except Exception as e:
                    results.append({'file': f.name, 'ok': False, 'error': str(e)})
        ok_count = sum(1 for r in results if r['ok'])
        return {'ok': True, 'converted': ok_count, 'total': len(results), 'results': results}

    # ─── ImageMagick CLI ─────────────────────────────────────────────────────

    def imagemagick(self, args: list) -> dict:
        """
        Произвольная команда ImageMagick convert / magick.
        Пример: args=['input.jpg', '-resize', '800x600', 'output.jpg']
        Скачать бесплатно: https://imagemagick.org/script/download.php
        Windows: winget install ImageMagick.ImageMagick
        """
        exe = _cmd('magick') or _cmd('convert')
        if not exe:
            return {
                'ok': False,
                'error': 'ImageMagick не найден.',
                'install': 'https://imagemagick.org/script/download.php  |  winget install ImageMagick.ImageMagick',
            }
        cli = [exe] + (['convert'] if exe.endswith('magick') else []) + [str(a) for a in args]
        try:
            r = subprocess.run(cli, capture_output=True, text=True, timeout=120)
            return {'ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── GIMP CLI ────────────────────────────────────────────────────────────

    def gimp_script(self, script: str) -> dict:
        """
        Запуск Script-Fu команды в GIMP batch-режиме (без GUI).
        script — строка Script-Fu.
        Пример: '(gimp-version)'
        Скачать бесплатно: https://www.gimp.org/downloads/
        """
        exe = _cmd('gimp') or _cmd('gimp-2.10') or _cmd('gimp-2.99')
        if not exe:
            return {
                'ok': False,
                'error': 'GIMP не найден.',
                'install': 'Скачай бесплатно: https://www.gimp.org/downloads/',
            }
        try:
            r = subprocess.run(
                [exe, '-i', '-b', script, '-b', '(gimp-quit 0)'],
                capture_output=True, text=True, timeout=120,
            )
            return {'ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── Inkscape CLI ────────────────────────────────────────────────────────

    def inkscape_export(self, svg_path: str, output_path: str,
                        dpi: int = 96, fmt: str = 'png') -> dict:
        """
        Конвертировать SVG в PNG / PDF / EPS / EMF через Inkscape CLI.
        Скачать бесплатно: https://inkscape.org/release/
        Windows: winget install Inkscape.Inkscape
        fmt: png | pdf | eps | emf
        """
        exe = _cmd('inkscape') or _cmd('inkscape.exe')
        if not exe:
            return {
                'ok': False,
                'error': 'Inkscape не найден.',
                'install': 'https://inkscape.org/release/  |  winget install Inkscape.Inkscape',
            }
        try:
            r = subprocess.run(
                [exe, svg_path, f'--export-filename={output_path}', f'--export-dpi={dpi}'],
                capture_output=True, text=True, timeout=120,
            )
            return {'ok': r.returncode == 0, 'output': output_path,
                    'stdout': r.stdout, 'stderr': r.stderr}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, action: str = 'info', **params) -> dict:
        """
        action:
          info | resize | crop | rotate | flip | grayscale | blur | sharpen |
          convert | add_text | watermark | thumbnail | batch_convert |
          imagemagick | gimp_script | inkscape_export
        """
        actions = {
            'info':            self.info,
            'resize':          self.resize,
            'crop':            self.crop,
            'rotate':          self.rotate,
            'flip':            self.flip,
            'grayscale':       self.grayscale,
            'blur':            self.blur,
            'sharpen':         self.sharpen,
            'convert':         self.convert,
            'add_text':        self.add_text,
            'watermark':       self.watermark,
            'thumbnail':       self.thumbnail,
            'batch_convert':   self.batch_convert,
            'imagemagick':     self.imagemagick,
            'gimp_script':     self.gimp_script,
            'inkscape_export': self.inkscape_export,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
