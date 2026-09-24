"""`convert_file` — программы для файлов клиента, запущенные безопасно.

Зачем. Замер 2026-09-24 на новом сервере: tesseract, LibreOffice, ffmpeg,
Inkscape, ImageMagick, Ghostscript/poppler и Blender стоят и работают, но
агенту недоступны — `shell_exec` пускает только git/grep/uv/…, лаборатории
запрещён subprocess. Скан, «переведи Word в PDF», видео — вне его рук.

Почему не открыть эти программы в `shell_exec`. Каждая исполняет чужой
файл, а у агента в рабочей папке лежат ключи:
  * ffmpeg: плейлист HLS/DASH внутри файла открывает любой путь или адрес
    (CVE-2023-6605) — лечится `-protocol_whitelist` и запретом плейлистов;
  * LibreOffice: в безоконном режиме макросы и обработчики событий не
    исполняются, но у форматов длинная история уязвимостей (CVE-2023-6186,
    CVE-2024-3044) — совет: бесправный пользователь в изолированной папке;
  * Inkscape: SVG с `xi:include` вставляет в результат содержимое любого
    локального файла (разбор elttam, «inkscape-xml»), разработчики считают
    это нормой — вход проверяет вызывающий;
  * Blender: `.blend` несёт скрипты; `--disable-autoexec` их выключает, но
    не все (руководство Blender, «Scripting & Security»);
  * ImageMagick: формат угадывается по содержимому (ImageTragick) — формат
    называется явно, `png:in.png`.

Устройство (моё инженерное решение по этим источникам):
  1. Только фиксированные действия (`op`) с фиксированными аргументами —
     своего аргумента программе не передать.
  2. Вход — файл ВНУТРИ рабочей папки (не файл ключей), копируется во
     временную папку; программа работает там ПОД ПОЛЬЗОВАТЕЛЕМ nobody —
     /root с ключами и кодом ей закрыт системой, а не договорённостью.
  3. Результат копируется в `converted/` рабочей папки. Ничего не
     перезаписывается: имя с меткой действия и времени.
  4. Нельзя сбросить права (не Linux, не root, нет setpriv) — отказ.
     Открытая дверь без замка хуже закрытой.

Риск: reversible — создаёт новые файлы в `converted/`, чужого не трогает.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.base import Risk, Tool, require_ascii_identifier
from tools.file_read import _is_credential_path

OUTPUT_DIR = "converted"
_MAX_INPUT_BYTES = 200 * 1024 * 1024
_MAX_TEXT_CHARS = 20000
_NOBODY = 65534
_TIMEOUT = {"ocr": 300, "office": 180, "pdf_pages": 180, "image": 120,
            "svg": 120, "media": 600, "render3d": 600}

_IMAGE_IN = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "gif")
#: Вход и выход каждого действия. Всё прочее — отказ до запуска.
OPS: dict[str, dict[str, tuple[str, ...]]] = {
    "ocr": {"in": (*_IMAGE_IN, "pdf"), "to": ("txt",)},
    "office": {"in": ("doc", "docx", "odt", "rtf", "txt", "html", "htm", "xls", "xlsx", "ods",
                      "csv", "ppt", "pptx", "odp"),
               "to": ("pdf", "docx", "odt", "xlsx", "ods", "csv", "txt", "html", "pptx")},
    "pdf_pages": {"in": ("pdf",), "to": ("png",)},
    "image": {"in": _IMAGE_IN, "to": ("png", "jpg", "webp", "pdf")},
    "svg": {"in": ("svg",), "to": ("png", "pdf")},
    "media": {"in": ("mp4", "mov", "mkv", "webm", "avi", "mp3", "wav", "ogg", "m4a", "flac", "gif"),
              "to": ("mp4", "webm", "mp3", "wav", "gif")},
    "render3d": {"in": ("blend",), "to": ("png",)},
}
_OCR_LANGS = ("rus", "eng", "rus+eng")
#: SVG, которые нельзя отдавать Inkscape: подключение файлов, сущности,
#: ссылки наружу (всё, кроме якоря «#…» и встроенного data:).
_SVG_DANGER = re.compile(r"xi:include|<!ENTITY|<!DOCTYPE|XInclude", re.IGNORECASE)
_SVG_HREF = re.compile(r"""(?:xlink:)?href\s*=\s*["']\s*([^"']*)""", re.IGNORECASE)


class ConvertRefused(ValueError):
    """Отказ до запуска программы — с причиной, которую видит агент."""


def _check_args(op: str, path: str, to: str | None, lang: str | None) -> tuple[str, str]:
    if op not in OPS:
        raise ConvertRefused(f"op must be one of {sorted(OPS)}, got {op!r}")
    require_ascii_identifier(path, role="convert_file path")
    p = Path(path)
    if p.is_absolute() or path.startswith(("/", "\\")) or ".." in p.parts:
        raise ConvertRefused(f"path must be relative and stay inside the workspace: {path!r}")
    if _is_credential_path(path):
        raise ConvertRefused(f"{path!r} is a credential store; it is never converted")
    ext = p.suffix.lower().lstrip(".")
    if ext not in OPS[op]["in"]:
        raise ConvertRefused(f"op={op} takes {OPS[op]['in']}, not .{ext}")
    target = (to or OPS[op]["to"][0]).lower().lstrip(".")
    if target not in OPS[op]["to"]:
        raise ConvertRefused(f"op={op} produces {OPS[op]['to']}, not {target!r}")
    if op == "ocr" and (lang or "rus+eng") not in _OCR_LANGS:
        raise ConvertRefused(f"lang must be one of {_OCR_LANGS}")
    return ext, target


def svg_refusal(text: str) -> str | None:
    """Причина не отдавать SVG программе, или None."""
    if _SVG_DANGER.search(text):
        return "SVG includes files or declares entities (xi:include / DOCTYPE / ENTITY)"
    for href in _SVG_HREF.findall(text):
        h = href.strip()
        if h and not h.startswith(("#", "data:")):
            return f"SVG links outside itself ({h[:60]!r}); only #anchors and data: are allowed"
    return None


def _argv(op: str, ext: str, to: str, *, lang: str, width: int | None, max_pages: int,
          max_seconds: int | None) -> list[list[str]]:
    """Команды для одного действия; вход всегда `in.<ext>`, выход — в `out/`."""
    src = f"in.{ext}"
    if op == "ocr":
        if ext == "pdf":
            return [["pdftoppm", "-r", "200", "-png", "-f", "1", "-l", str(max_pages), src, "page"],
                    ["__ocr_pages__", lang]]
        return [["tesseract", src, "out/result", "-l", lang]]
    if op == "office":
        return [["soffice", "--headless", "--norestore", "--nolockcheck",
                 "-env:UserInstallation=file://" + "__TMP__/lo_profile",
                 "--convert-to", to, "--outdir", "out", src]]
    if op == "pdf_pages":
        return [["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", str(max_pages), src, "out/page"]]
    if op == "image":
        size = ["-resize", f"{width}x"] if width else []
        return [["convert", f"{ext}:{src}", *size, f"{to}:out/result.{to}"]]
    if op == "svg":
        return [["inkscape", src, f"--export-type={to}", f"--export-filename=out/result.{to}"]]
    if op == "media":
        limit = ["-t", str(max_seconds)] if max_seconds else []
        return [["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file",
                 "-i", src, *limit, "-y", f"out/result.{to}"]]
    # render3d: без автозапуска скриптов и без пользовательских настроек.
    return [["blender", "--background", "--factory-startup", "--disable-autoexec", src,
             "--render-output", "out/frame_", "--render-format", "PNG", "--render-frame", "1"]]


def sandbox_available() -> str | None:
    """Почему программы запускать нельзя, или None, если можно."""
    if not sys.platform.startswith("linux"):
        return "the sandbox needs Linux"
    if os.geteuid() != 0:
        return "the sandbox needs root to drop to the nobody user"
    if not shutil.which("setpriv"):
        return "setpriv (util-linux) is not installed"
    return None


def run_sandboxed(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Запустить команду пользователем nobody в `cwd`; без оболочки и без ключей."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(cwd),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "OMP_NUM_THREADS": "2"}
    cmd = ["setpriv", f"--reuid={_NOBODY}", f"--regid={_NOBODY}", "--clear-groups",
           "--no-new-privs", *argv]
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,  # noqa: S603 — argv из фиксированных частей
                           encoding="utf-8", errors="replace", timeout=timeout, check=False)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-2000:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout {timeout}s"


class ConvertFileTool(Tool):
    """OCR, конвертация документов, страницы PDF, картинки, SVG, видео, 3D."""

    name = "convert_file"
    description = (
        "Run a file program on a workspace file safely: op=ocr (image/pdf -> text), "
        "office (doc/xls/ppt/odt/csv/html -> pdf/docx/xlsx/csv/txt/html), pdf_pages "
        "(pdf -> png pages), image (resize/convert), svg (-> png/pdf), media "
        "(audio/video -> mp4/webm/mp3/wav/gif), render3d (.blend -> png). The result "
        "goes to converted/. Risk: reversible."
    )
    risk: Risk = "reversible"

    def __init__(self, workspace_root: Path, *, runner: Any = None):
        self.workspace_root = Path(workspace_root).resolve()
        self._runner = runner or run_sandboxed

    def run(self, op: str, path: str, to: str | None = None, lang: str | None = None,
            width: int | None = None, max_pages: int = 20, max_seconds: int | None = None,
            ) -> dict[str, Any]:
        ext, target = _check_args(op, path, to, lang)
        src = (self.workspace_root / path).resolve()
        if self.workspace_root not in src.parents or not src.is_file():
            raise ConvertRefused(f"not a file inside the workspace: {path!r}")
        if src.stat().st_size > _MAX_INPUT_BYTES:
            raise ConvertRefused(f"{path!r} is larger than {_MAX_INPUT_BYTES // (1024 * 1024)} MB")
        if op == "svg" and (why := svg_refusal(src.read_text(encoding="utf-8", errors="replace"))):
            raise ConvertRefused(why)
        if self._runner is run_sandboxed and (why := sandbox_available()):
            raise ConvertRefused(f"programs are not run without the sandbox: {why}")
        width = max(16, min(int(width), 8000)) if width else None
        max_pages = max(1, min(int(max_pages), 50))
        max_seconds = max(1, min(int(max_seconds), 600)) if max_seconds else None
        lang = lang or "rus+eng"
        tmp = Path(tempfile.mkdtemp(prefix="convert_file_"))
        try:
            (tmp / "out").mkdir()
            shutil.copyfile(src, tmp / f"in.{ext}")
            if self._runner is run_sandboxed:
                for p in (tmp, tmp / "out", tmp / f"in.{ext}"):
                    os.chown(p, _NOBODY, _NOBODY)
            code, log = 0, ""
            for argv in _argv(op, ext, target, lang=lang, width=width,
                              max_pages=max_pages, max_seconds=max_seconds):
                argv = [a.replace("__TMP__", str(tmp)) for a in argv]
                if argv[0] == "__ocr_pages__":
                    code, log = self._ocr_pages(tmp, argv[1], _TIMEOUT[op])
                else:
                    code, log = self._runner(argv, tmp, _TIMEOUT[op])
                if code != 0:
                    break
            outputs = self._collect(tmp / "out", op, Path(path).stem)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        result: dict[str, Any] = {"op": op, "input": path, "exit_code": code,
                                  "outputs": outputs, "log_tail": log[-600:]}
        if op == "ocr" and outputs:
            text = (self.workspace_root / outputs[0]).read_text(encoding="utf-8", errors="replace")
            result["text"] = text[:_MAX_TEXT_CHARS]
            result["text_truncated"] = len(text) > _MAX_TEXT_CHARS
        if not outputs:
            result["error"] = f"{op} produced no output (exit {code})"
        return result

    def _ocr_pages(self, tmp: Path, lang: str, timeout: int) -> tuple[int, str]:
        pages = sorted(tmp.glob("page-*.png"))
        if not pages:
            return 1, "pdftoppm produced no pages"
        texts, log = [], ""
        for i, page in enumerate(pages, 1):
            code, log = self._runner(["tesseract", page.name, f"p{i:03d}", "-l", lang], tmp, timeout)
            if code != 0:
                return code, log
            texts.append(f"--- page {i} ---\n" + (tmp / f"p{i:03d}.txt").read_text(
                encoding="utf-8", errors="replace"))
        (tmp / "out" / "result.txt").write_text("\n".join(texts), encoding="utf-8")
        return 0, log

    def _collect(self, out: Path, op: str, stem: str) -> list[str]:
        files = sorted(p for p in out.iterdir() if p.is_file()) if out.is_dir() else []
        if not files:
            return []
        dest_dir = self.workspace_root / OUTPUT_DIR
        dest_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        saved: list[str] = []
        for i, f in enumerate(files):
            suffix = f"_{i + 1}" if len(files) > 1 else ""
            name = f"{stem}__{op}_{stamp}{suffix}{f.suffix.lower()}"
            shutil.copyfile(f, dest_dir / name)
            saved.append(f"{OUTPUT_DIR}/{name}")
        return saved


def sanitize_args(args: dict[str, Any], idx: int, warnings: list[str]) -> dict[str, Any] | None:
    """Приём шага плана: обязательные op и path, всё прочее — по списку."""
    op, path = args.get("op"), args.get("path")
    if not isinstance(op, str) or not isinstance(path, str) or not path.strip():
        warnings.append(f"step[{idx}]: convert_file needs op and path, dropped")
        return None
    clean = {"op": op.strip(), "path": path.strip()}
    for key in ("to", "lang"):
        if isinstance(args.get(key), str) and args[key].strip():
            clean[key] = args[key].strip()
    for key in ("width", "max_pages", "max_seconds"):
        if isinstance(args.get(key), int):
            clean[key] = args[key]
    try:
        _check_args(clean["op"], clean["path"], clean.get("to"), clean.get("lang"))
    except ValueError as exc:
        warnings.append(f"step[{idx}]: convert_file refused: {exc}; dropped")
        return None
    return {"tool": "convert_file", "arguments": clean,
            "label": f"convert_file:{clean['op']}:{clean['path'][:60]}",
            "expected_outcome": "The converted file saved under converted/ (or OCR text)."}
