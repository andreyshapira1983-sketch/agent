"""
CADTool — работа с САПР/CAD: DXF чертежи, OpenSCAD 3D, AutoCAD COM.

Бесплатные движки:
  1. ezdxf (pip install ezdxf) — создание/чтение/редактирование DXF файлов
       DXF открывается в: AutoCAD, LibreCAD (бесплатно), FreeCAD (бесплатно),
       QCAD (бесплатно), Autodesk Viewer онлайн.
  2. OpenSCAD CLI (https://openscad.org/) — параметрическое 3D моделирование
       STL для 3D-печати, DXF для лазерной резки.
       Windows: winget install OpenSCAD.OpenSCAD

Платные (если установлены):
  3. AutoCAD COM API (Windows) — управление AutoCAD через win32com.

Переменные окружения:
  OPENSCAD_PATH=C:\\Program Files\\OpenSCAD\\openscad.exe  (если не в PATH)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from tools.tool_layer import BaseTool


def _ezdxf_ok() -> bool:
    try:
        import ezdxf  # noqa: F401
        return True
    except ImportError:
        return False


def _find_openscad(custom: str | None = None) -> str | None:
    if custom and os.path.exists(custom):
        return custom
    for cmd in ('openscad', 'openscad.exe'):
        found = shutil.which(cmd)
        if found:
            return found
    for c in [
        r'C:\Program Files\OpenSCAD\openscad.exe',
        '/usr/bin/openscad',
        '/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD',
    ]:
        if os.path.exists(c):
            return c
    return None


class CADTool(BaseTool):
    name = 'cad'
    description = (
        'Работа с CAD: создание/чтение DXF чертежей через ezdxf (бесплатно), '
        '3D моделирование через OpenSCAD (бесплатно), '
        'управление AutoCAD через COM API (если AutoCAD установлен).'
    )

    def __init__(self, openscad_path: str | None = None):
        super().__init__(name=self.name, description=self.description)
        path = openscad_path or os.environ.get('OPENSCAD_PATH')
        self._openscad = _find_openscad(path)

    # ─── ezdxf: DXF операции ─────────────────────────────────────────────────

    def create_dxf(self, output: str, entities: list) -> dict:
        """
        Создать DXF файл (чертёж) из примитивов.

        entities — список словарей:
          {'type': 'line',     'start': [0,0],   'end': [100,100]}
          {'type': 'circle',   'center': [50,50], 'radius': 25}
          {'type': 'arc',      'center': [50,50], 'radius': 25, 'start_angle': 0, 'end_angle': 180}
          {'type': 'text',     'text': 'Hello',   'insert': [10,10], 'height': 5}
          {'type': 'polyline', 'points': [[0,0],[100,0],[100,100]], 'close': True}
          {'type': 'rect',     'x': 10, 'y': 10,  'width': 80, 'height': 60}

        DXF открывается в LibreCAD / FreeCAD / AutoCAD / QCAD.
        """
        if not _ezdxf_ok():
            return {'ok': False, 'error': 'ezdxf не установлен.', 'install': 'pip install ezdxf'}
        import ezdxf
        doc = ezdxf.new('R2010')  # type: ignore[attr-defined]
        msp = doc.modelspace()
        for ent in entities:
            t = ent.get('type', '').lower()
            try:
                if t == 'line':
                    msp.add_line(ent['start'], ent['end'])
                elif t == 'circle':
                    msp.add_circle(ent['center'], ent['radius'])
                elif t == 'arc':
                    msp.add_arc(ent['center'], ent['radius'],
                                ent.get('start_angle', 0), ent.get('end_angle', 180))
                elif t == 'text':
                    msp.add_text(
                        ent['text'],
                        dxfattribs={
                            'insert': ent.get('insert', [0, 0]),
                            'height': ent.get('height', 2.5),
                        },
                    )
                elif t == 'polyline':
                    msp.add_lwpolyline(ent['points'], close=ent.get('close', False))
                elif t == 'rect':
                    x1, y1 = ent['x'], ent['y']
                    x2, y2 = x1 + ent['width'], y1 + ent['height']
                    msp.add_lwpolyline(
                        [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True
                    )
                else:
                    return {'ok': False, 'error': f'Неизвестный тип примитива: {t}'}
            except Exception as e:
                return {'ok': False, 'error': f'Ошибка при {t}: {e}'}
        doc.saveas(output)
        return {'ok': True, 'output': output, 'entities': len(entities)}

    def read_dxf(self, path: str) -> dict:
        """Читать DXF файл, вернуть информацию о примитивах."""
        if not _ezdxf_ok():
            return {'ok': False, 'error': 'ezdxf не установлен. pip install ezdxf'}
        import ezdxf
        try:
            doc = ezdxf.readfile(path)  # type: ignore[attr-defined]
            msp = doc.modelspace()
            counts: dict = {}
            sample = []
            for e in msp:
                dt = e.dxftype()
                counts[dt] = counts.get(dt, 0) + 1
                if len(sample) < 100:
                    info: dict = {'type': dt}
                    for attr in ('start', 'end', 'center', 'radius', 'text', 'insert'):
                        if hasattr(e.dxf, attr):
                            val = getattr(e.dxf, attr)
                            info[attr] = list(val) if hasattr(val, '__iter__') and not isinstance(val, str) else val
                    sample.append(info)
            return {
                'ok': True, 'path': path,
                'dxf_version': doc.dxfversion,
                'entity_counts': counts,
                'sample': sample,
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def dxf_to_svg(self, dxf_path: str, svg_path: str) -> dict:
        """Конвертировать DXF в SVG (для предпросмотра в браузере)."""
        if not _ezdxf_ok():
            return {'ok': False, 'error': 'ezdxf не установлен. pip install ezdxf'}
        try:
            import ezdxf
            from ezdxf.addons.drawing.frontend import Frontend
            from ezdxf.addons.drawing.properties import RenderContext
            from ezdxf.addons.drawing.svg import SVGBackend
            from ezdxf.addons.drawing.layout import Page, Settings
            doc = ezdxf.readfile(dxf_path)  # type: ignore[attr-defined]
            msp = doc.modelspace()
            backend = SVGBackend()
            page = Page.from_dxf_layout(msp)  # type: ignore[arg-type]
            fe = Frontend(RenderContext(doc), backend)  # type: ignore[call-arg]
            fe.draw_layout(msp)
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(backend.get_string(page, settings=Settings()))  # pyright: ignore[reportCallIssue]
            return {'ok': True, 'output': svg_path}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── OpenSCAD ────────────────────────────────────────────────────────────

    def openscad_render(self, scad_script: str, output: str,
                        timeout: int = 120) -> dict:
        """
        Рендеринг OpenSCAD-скрипта → STL / PNG / DXF.
        scad_script — код OpenSCAD (строка).
        output — путь к результату (.stl для печати, .png для превью, .dxf для лазерной резки).
        Скачать бесплатно: https://openscad.org/downloads.html
        Windows: winget install OpenSCAD.OpenSCAD
        """
        if not self._openscad:
            return {
                'ok': False,
                'error': 'OpenSCAD не найден.',
                'install': 'https://openscad.org/downloads.html',
                'windows': 'winget install OpenSCAD.OpenSCAD',
                'env_hint': 'Или укажи OPENSCAD_PATH в .env',
            }
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.scad', delete=False, encoding='utf-8'
        ) as f:
            f.write(scad_script)
            scad_file = f.name
        try:
            r = subprocess.run(  # noqa: S603
                [self._openscad, '-o', output, scad_file],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            return {
                'ok': r.returncode == 0, 'output': output,
                'stdout': r.stdout, 'stderr': r.stderr,
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        finally:
            try:
                os.unlink(scad_file)
            except Exception:
                pass

    def openscad_file(self, scad_path: str, output: str, timeout: int = 120) -> dict:
        """Рендеринг существующего .scad файла."""
        if not self._openscad:
            return {'ok': False, 'error': 'OpenSCAD не найден. https://openscad.org/downloads.html'}
        try:
            r = subprocess.run(  # noqa: S603
                [self._openscad, '-o', output, scad_path],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            return {
                'ok': r.returncode == 0, 'output': output,
                'stdout': r.stdout, 'stderr': r.stderr,
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── AutoCAD COM (Windows, если AutoCAD установлен) ──────────────────────

    def autocad_run_script(self, script_path: str) -> dict:
        """
        Запустить .scr или .lsp скрипт через AutoCAD COM API.
        AutoCAD — ПЛАТНЫЙ продукт Autodesk.
        Бесплатные альтернативы: LibreCAD, FreeCAD, QCAD.
        """
        try:
            import win32com.client  # type: ignore
            acad = win32com.client.Dispatch('AutoCAD.Application')
            acad.Visible = False
            doc = acad.Documents.Open(os.path.abspath(script_path))
            doc.Close(False)
            return {'ok': True, 'message': 'Скрипт выполнен через AutoCAD COM'}
        except ImportError:
            return {'ok': False, 'error': 'pywin32 не установлен.', 'install': 'pip install pywin32'}
        except Exception as e:
            msg = str(e)
            if 'Invalid class' in msg or 'Invalid ProgId' in msg or 'class not registered' in msg.lower():
                return {
                    'ok': False,
                    'error': 'AutoCAD не установлен (платная программа Autodesk).',
                    'free_alternatives': 'LibreCAD (https://librecad.org), FreeCAD (https://freecad.org)',
                    'alternative_tool': 'Используй create_dxf() для создания чертежей бесплатно.',
                }
            return {'ok': False, 'error': msg}

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, *args, action: str = 'read_dxf', **params) -> dict:
        """
        action:
          create_dxf | read_dxf | dxf_to_svg |
          openscad_render | openscad_file |
          autocad_run_script
        """
        actions = {
            'create_dxf':         self.create_dxf,
            'read_dxf':           self.read_dxf,
            'dxf_to_svg':         self.dxf_to_svg,
            'openscad_render':    self.openscad_render,
            'openscad_file':      self.openscad_file,
            'autocad_run_script': self.autocad_run_script,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
