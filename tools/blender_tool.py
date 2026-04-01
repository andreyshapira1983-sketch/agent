"""
BlenderTool — управление Blender в headless-режиме (без GUI).

Blender ПОЛНОСТЬЮ БЕСПЛАТЕН: https://www.blender.org/download/
Поддерживает Python API (bpy), рендеринг сцен,
импорт/экспорт OBJ/FBX/GLTF/STL/DAE, процедурное моделирование.

Переменная окружения:
  BLENDER_PATH=C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe
  (если не задана — ищем автоматически в стандартных местах)

Windows: winget install BlenderFoundation.Blender
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from tools.tool_layer import BaseTool


def _find_blender(custom: str | None = None) -> str | None:
    if custom and os.path.exists(custom):
        return custom
    for cmd in ('blender', 'blender.exe'):
        found = shutil.which(cmd)
        if found:
            return found
    candidates = [
        r'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.4\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.1\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.0\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 3.6\blender.exe',
        '/usr/bin/blender',
        '/usr/local/bin/blender',
        '/Applications/Blender.app/Contents/MacOS/Blender',
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _run_blender(exe: str | None, args: list, timeout: int = 300) -> dict:
    if not exe:
        return {
            'ok': False,
            'error': 'Blender не найден.',
            'install': 'Скачай бесплатно: https://www.blender.org/download/',
            'windows': 'winget install BlenderFoundation.Blender',
            'env_hint': 'Или укажи BLENDER_PATH в .env',
        }
    try:
        r = subprocess.run(
            [exe] + args,
            capture_output=True, text=True, errors='replace', timeout=timeout,
        )
        return {
            'ok': r.returncode == 0,
            'stdout': r.stdout[-3000:],
            'stderr': r.stderr[-3000:],
        }
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'Таймаут {timeout}с превышен'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


class BlenderTool(BaseTool):
    name = 'blender'
    description = (
        'Управление Blender (3D, БЕСПЛАТНО): рендеринг сцен (CYCLES/EEVEE), '
        'импорт/экспорт 3D моделей (OBJ/FBX/GLTF/STL/DAE), '
        'запуск Python-скриптов bpy, конвертация форматов.'
    )

    def __init__(self, blender_path: str | None = None):
        path = blender_path or os.environ.get('BLENDER_PATH')
        self._exe = _find_blender(path)

    def version(self) -> dict:
        """Версия установленного Blender."""
        return _run_blender(self._exe, ['--version'], timeout=15)

    def render(self, blend_file: str, output: str,
               frame_start: int = 1, frame_end: int = 1,
               engine: str = 'CYCLES') -> dict:
        """
        Рендеринг .blend файла.
        engine: CYCLES (фотореализм, медленно) | BLENDER_EEVEE (быстро).
        output — путь без расширения (Blender добавит номер кадра + расширение).
        """
        script = (
            f"import bpy\n"
            f"bpy.context.scene.render.engine = '{engine}'\n"
            f"bpy.context.scene.render.filepath = r'{output}'\n"
            f"bpy.context.scene.frame_start = {frame_start}\n"
            f"bpy.context.scene.frame_end = {frame_end}\n"
            f"bpy.ops.render.render(animation=True)\n"
        )
        return self._script(blend_file, script)

    def export(self, blend_file: str, output: str, fmt: str = 'obj') -> dict:
        """
        Экспорт сцены в 3D формат.
        fmt: obj | fbx | gltf | stl | dae | ply
        """
        ops = {
            'obj':  f"bpy.ops.export_scene.obj(filepath=r'{output}')",
            'fbx':  f"bpy.ops.export_scene.fbx(filepath=r'{output}')",
            'gltf': f"bpy.ops.export_scene.gltf(filepath=r'{output}')",
            'stl':  f"bpy.ops.export_mesh.stl(filepath=r'{output}')",
            'dae':  f"bpy.ops.wm.collada_export(filepath=r'{output}')",
            'ply':  f"bpy.ops.export_mesh.ply(filepath=r'{output}')",
        }
        op = ops.get(fmt.lower())
        if not op:
            return {'ok': False, 'error': f'Формат не поддерживается: {fmt}. Доступные: {list(ops)}'}
        return self._script(blend_file, f'import bpy\n{op}')

    def convert(self, input_path: str, output_path: str) -> dict:
        """
        Конвертировать между 3D форматами.
        Форматы определяются по расширению файлов (obj/fbx/gltf/glb/stl/dae).
        """
        in_ext = os.path.splitext(input_path)[1].lstrip('.').lower()
        out_ext = os.path.splitext(output_path)[1].lstrip('.').lower()
        imports = {
            'obj':  f"bpy.ops.import_scene.obj(filepath=r'{input_path}')",
            'fbx':  f"bpy.ops.import_scene.fbx(filepath=r'{input_path}')",
            'stl':  f"bpy.ops.import_mesh.stl(filepath=r'{input_path}')",
            'dae':  f"bpy.ops.wm.collada_import(filepath=r'{input_path}')",
            'gltf': f"bpy.ops.import_scene.gltf(filepath=r'{input_path}')",
            'glb':  f"bpy.ops.import_scene.gltf(filepath=r'{input_path}')",
        }
        exports = {
            'obj':  f"bpy.ops.export_scene.obj(filepath=r'{output_path}')",
            'fbx':  f"bpy.ops.export_scene.fbx(filepath=r'{output_path}')",
            'gltf': f"bpy.ops.export_scene.gltf(filepath=r'{output_path}')",
            'stl':  f"bpy.ops.export_mesh.stl(filepath=r'{output_path}')",
            'dae':  f"bpy.ops.wm.collada_export(filepath=r'{output_path}')",
            'ply':  f"bpy.ops.export_mesh.ply(filepath=r'{output_path}')",
        }
        imp = imports.get(in_ext)
        exp = exports.get(out_ext)
        if not imp:
            return {'ok': False, 'error': f'Импорт из .{in_ext} не поддерживается'}
        if not exp:
            return {'ok': False, 'error': f'Экспорт в .{out_ext} не поддерживается'}
        script = (
            "import bpy\n"
            "bpy.ops.object.select_all(action='SELECT')\n"
            "bpy.ops.object.delete()\n"
            f"{imp}\n"
            f"{exp}\n"
        )
        return self._script(None, script)

    def run_python(self, script: str, blend_file: str | None = None) -> dict:
        """
        Выполнить произвольный Python-скрипт через bpy.
        script — Python-код как строка.
        blend_file — открыть .blend перед выполнением (опционально).
        """
        return self._script(blend_file, script)

    def _script(self, blend_file: str | None, script: str, timeout: int = 300) -> dict:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            f.write(script)
            script_path = f.name
        try:
            args = ['--background']
            if blend_file:
                args.append(blend_file)
            args += ['--python', script_path]
            return _run_blender(self._exe, args, timeout=timeout)
        finally:
            try:
                os.unlink(script_path)
            except Exception:
                pass

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, action: str = 'version', **params) -> dict:
        """
        action: version | render | export | convert | run_python
        """
        actions = {
            'version':    self.version,
            'render':     self.render,
            'export':     self.export,
            'convert':    self.convert,
            'run_python': self.run_python,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
