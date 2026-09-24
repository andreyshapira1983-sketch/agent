"""convert_file: программы для файлов клиента — только фиксированные действия в песочнице.

Замер 2026-09-24: tesseract, LibreOffice, ffmpeg, Inkscape, ImageMagick,
poppler и Blender стояли на сервере и работали, но агенту были недоступны;
открыть их в shell_exec нельзя — чужой файл исполняется программой рядом с
ключами (ffmpeg HLS → чтение путей, CVE-2023-6605; SVG xi:include →
содержимое локального файла в результате; .blend несёт скрипты).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.convert_file import (
    ConvertFileTool,
    ConvertRefused,
    _argv,
    run_sandboxed,
    sandbox_available,
    sanitize_args,
    svg_refusal,
)

_SANDBOX = sandbox_available() is None


# --- 1. отказ до запуска --------------------------------------------------

@pytest.mark.parametrize(("op", "path", "to", "needle"), [
    ("shell", "a.pdf", None, "op must be"),
    ("ocr", "/etc/passwd.png", None, "relative"),
    ("ocr", "../x.png", None, "relative"),
    ("office", ".env.docx", None, "credential"),
    ("office", "notes.exe", None, "takes"),
    ("office", "a.docx", "exe", "produces"),
])
def test_a_bad_request_is_refused_before_any_program(tmp_path: Path, op, path, to, needle) -> None:
    with pytest.raises(ValueError, match=needle):
        ConvertFileTool(workspace_root=tmp_path, runner=lambda *a: (0, "")).run(op=op, path=path, to=to)


def test_an_unknown_ocr_language_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(ConvertRefused, match="lang"):
        ConvertFileTool(workspace_root=tmp_path, runner=lambda *a: (0, "")).run(
            op="ocr", path="a.png", lang="deu")


@pytest.mark.parametrize("svg", [
    '<svg xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include parse="text" href="file:///root/agent-main/.env"/></svg>',
    '<!DOCTYPE svg [<!ENTITY k SYSTEM "file:///etc/passwd">]><svg>&k;</svg>',
    '<svg><image xlink:href="/root/agent-main/.env"/></svg>',
    '<svg><image href="file:///etc/hostname"/></svg>',
])
def test_a_dangerous_svg_is_refused(svg: str) -> None:
    """Разбор elttam «inkscape-xml»: xi:include вставляет в PDF содержимое файла."""
    assert svg_refusal(svg)


def test_a_plain_svg_passes() -> None:
    assert svg_refusal('<svg><use href="#a"/><image href="data:image/png;base64,AAA="/></svg>') is None


def test_without_the_sandbox_programs_do_not_run(tmp_path: Path) -> None:
    """Открытая дверь без замка хуже закрытой: нет песочницы — отказ."""
    if _SANDBOX:
        pytest.skip("здесь песочница есть")
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(ConvertRefused, match="sandbox"):
        ConvertFileTool(workspace_root=tmp_path).run(op="ocr", path="a.png")


def test_the_sanitizer_admits_only_checked_steps() -> None:
    warnings: list[str] = []
    assert sanitize_args({"op": "ocr", "path": "../x.png"}, 0, warnings) is None
    step = sanitize_args({"op": "office", "path": "inbox/a.docx", "to": "pdf", "rm": "-rf"}, 1, warnings)
    assert step and step["arguments"] == {"op": "office", "path": "inbox/a.docx", "to": "pdf"}


# --- 2. фиксированные команды ----------------------------------------------

def _cmd(op: str, ext: str, to: str) -> list[str]:
    return _argv(op, ext, to, lang="rus+eng", width=None, max_pages=5, max_seconds=None)[0]


def test_ffmpeg_cannot_open_other_files_or_urls() -> None:
    """CVE-2023-6605: плейлист внутри файла открывает любые пути — только протокол file."""
    cmd = _cmd("media", "mp4", "gif")
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")


def test_blender_runs_no_scripts_from_the_file() -> None:
    cmd = _cmd("render3d", "blend", "png")
    assert "--disable-autoexec" in cmd and "--factory-startup" in cmd
    assert cmd.index("--disable-autoexec") < cmd.index("in.blend")


def test_imagemagick_is_told_the_format() -> None:
    """ImageTragick: формат по содержимому не угадывается — назван явно."""
    assert "png:in.png" in _cmd("image", "png", "jpg")


def test_a_result_lands_in_converted_and_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "a.docx").write_bytes(b"x")

    def fake(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        (cwd / "out" / "in.pdf").write_bytes(b"%PDF")
        return 0, ""

    tool = ConvertFileTool(workspace_root=tmp_path, runner=fake)
    first = tool.run(op="office", path="inbox/a.docx", to="pdf")["outputs"]
    assert first and first[0].startswith("converted/a__office_") and first[0].endswith(".pdf")
    assert (tmp_path / first[0]).read_bytes() == b"%PDF"


# --- 3. настоящие программы в песочнице (сервер) ----------------------------

needs_sandbox = pytest.mark.skipif(not _SANDBOX, reason="нужна песочница: Linux, root, setpriv")


@needs_sandbox
def test_the_sandbox_cannot_read_a_root_only_file(tmp_path: Path) -> None:
    """Главное: программа под nobody не читает закрытый файл (как ключи в /root)."""
    secret = tmp_path / "secret.env"
    secret.write_text("KEY=do-not-leak\n", encoding="utf-8")
    secret.chmod(0o600)
    work = Path(tempfile.mkdtemp())
    shutil.chown(work, 65534, 65534)
    try:
        code, out = run_sandboxed(["cat", str(secret)], work, 10)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    assert code != 0 and "do-not-leak" not in out


def _needs(*programs: str):
    missing = [p for p in programs if not shutil.which(p)]
    return pytest.mark.skipif(bool(missing), reason=f"нет программ: {missing}")


@needs_sandbox
@_needs("tesseract")
def test_ocr_reads_russian_text(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    fonts = list(Path("/usr/share/fonts").rglob("DejaVuSans.ttf"))
    img = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(img).text((20, 40), "Счёт 1250 рублей", fill="black",
                             font=ImageFont.truetype(str(fonts[0]), 48) if fonts else None)
    img.save(tmp_path / "scan.png")
    result = ConvertFileTool(workspace_root=tmp_path).run(op="ocr", path="scan.png")
    assert result["exit_code"] == 0, result
    assert "1250" in result["text"] and "руб" in result["text"]


@needs_sandbox
@_needs("soffice")
def test_office_turns_a_docx_into_a_pdf(tmp_path: Path) -> None:
    import docx
    d = docx.Document()
    d.add_paragraph("Проверка конвертации")
    d.save(tmp_path / "report.docx")
    result = ConvertFileTool(workspace_root=tmp_path).run(op="office", path="report.docx", to="pdf")
    assert result["exit_code"] == 0 and result["outputs"], result
    assert (tmp_path / result["outputs"][0]).read_bytes()[:4] == b"%PDF"


@needs_sandbox
@_needs("ffmpeg")
def test_media_makes_a_gif(tmp_path: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S603, S607
                    "testsrc=duration=1:size=160x120:rate=5", "-y", str(tmp_path / "clip.mp4")], check=True)
    result = ConvertFileTool(workspace_root=tmp_path).run(op="media", path="clip.mp4", to="gif")
    assert result["exit_code"] == 0 and result["outputs"], result


@needs_sandbox
@_needs("inkscape")
def test_svg_becomes_a_png(tmp_path: Path) -> None:
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<rect width="40" height="40" fill="red"/></svg>', encoding="utf-8")
    result = ConvertFileTool(workspace_root=tmp_path).run(op="svg", path="logo.svg", to="png")
    assert result["exit_code"] == 0 and result["outputs"], result


@needs_sandbox
@_needs("pdftoppm", "soffice")
def test_pdf_pages_become_images(tmp_path: Path) -> None:
    import docx
    d = docx.Document()
    d.add_paragraph("Страница")
    d.save(tmp_path / "p.docx")
    tool = ConvertFileTool(workspace_root=tmp_path)
    pdf = tool.run(op="office", path="p.docx", to="pdf")["outputs"][0]
    result = tool.run(op="pdf_pages", path=pdf)
    assert result["exit_code"] == 0 and result["outputs"][0].endswith(".png"), result


@needs_sandbox
@_needs("convert")
def test_image_is_resized(tmp_path: Path) -> None:
    from PIL import Image
    Image.new("RGB", (400, 200), "blue").save(tmp_path / "big.png")
    result = ConvertFileTool(workspace_root=tmp_path).run(op="image", path="big.png", to="jpg", width=100)
    assert result["exit_code"] == 0 and result["outputs"], result
    assert Image.open(tmp_path / result["outputs"][0]).size[0] == 100


@needs_sandbox
@_needs("blender")
def test_a_blend_file_is_rendered(tmp_path: Path) -> None:
    blend = tmp_path / "scene.blend"
    script = ("import bpy; s=bpy.context.scene; s.render.engine='CYCLES'; s.cycles.samples=2; "
              "s.cycles.use_denoising=False; s.render.resolution_x=80; s.render.resolution_y=60; "
              f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})")
    subprocess.run(["blender", "--background", "--factory-startup", "--python-expr", script],  # noqa: S603, S607
                   check=True, capture_output=True, timeout=300)
    result = ConvertFileTool(workspace_root=tmp_path).run(op="render3d", path="scene.blend")
    assert result["exit_code"] == 0 and result["outputs"][0].endswith(".png"), result


def test_a_text_result_comes_back_in_the_output(tmp_path: Path) -> None:
    """Приёмка урока 1, 24.09: текст Word -> txt лежал только файлом, агент его не
    дочитал и сравнил итоги, не видя одной стороны."""
    (tmp_path / "a.docx").write_bytes(b"x")

    def fake(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        (cwd / "out" / "in.txt").write_text("ИТОГО: 48 750 руб.", encoding="utf-8")
        return 0, ""

    result = ConvertFileTool(workspace_root=tmp_path, runner=fake).run(op="office", path="a.docx", to="txt")
    assert "48 750" in result["text"]


def test_a_byte_order_mark_does_not_reach_the_text(tmp_path: Path) -> None:
    """24.09: LibreOffice пишет txt с U+FEFF; сторож внедрений счёл его скрытым
    текстом, и числа из Word не стали уликой."""
    (tmp_path / "a.docx").write_bytes(b"x")

    def fake(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        (cwd / "out" / "in.txt").write_text("﻿ИТОГО: 48 750", encoding="utf-8")
        return 0, ""

    text = ConvertFileTool(workspace_root=tmp_path, runner=fake).run(op="office", path="a.docx", to="txt")["text"]
    assert not text.startswith("﻿") and text.startswith("ИТОГО")
