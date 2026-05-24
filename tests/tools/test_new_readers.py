"""tests/tools/test_new_readers.py — тесты PptxReaderTool, OdtReaderTool, MediaReaderTool"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
from pathlib import Path
import pytest

from tools.builtins.pptx_reader import PptxReaderTool
from tools.builtins.odt_reader import OdtReaderTool
from tools.builtins.media_reader import MediaReaderTool


# ══════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════════════

def _make_pptx_mock(slides_data: list[list[str]]) -> MagicMock:
    """Создаёт мок Presentation с заданным текстом по слайдам."""
    slides = []
    for slide_texts in slides_data:
        shapes = []
        for text in slide_texts:
            para = MagicMock()
            run = MagicMock()
            run.text = text
            para.runs = [run]
            para.paragraphs = [para]

            tf = MagicMock()
            tf.paragraphs = [para]

            shape = MagicMock()
            shape.has_text_frame = True
            shape.text_frame = tf
            shape.name = "Title" if text.startswith("Заголовок") else "Content"
            shapes.append(shape)

        slide = MagicMock()
        slide.shapes = shapes
        slide.has_notes_slide = False
        slides.append(slide)

    mock_prs = MagicMock()
    mock_prs.slides = slides
    mock_prs.core_properties.title = "Тестовая Презентация"
    mock_prs.core_properties.author = "Тестер"
    mock_prs.core_properties.subject = None
    mock_prs.core_properties.description = None
    mock_prs.core_properties.created = None
    mock_prs.core_properties.modified = None
    mock_prs.slide_width.inches = 10.0
    mock_prs.slide_height.inches = 7.5
    return mock_prs


def _make_mutagen_mock(
    duration: float = 180.0,
    bitrate: int = 128_000,
    sample_rate: int = 44100,
    channels: int = 2,
    tags: dict | None = None,
) -> MagicMock:
    """Создаёт мок mutagen.File()."""
    mock_info = MagicMock()
    mock_info.length = duration
    mock_info.bitrate = bitrate
    mock_info.sample_rate = sample_rate
    mock_info.channels = channels

    mock_file = MagicMock()
    mock_file.info = mock_info
    mock_file.tags = tags or {}

    # Easy mode tags
    _tags = tags or {}
    mock_file.get = lambda k, default=None: (
        [_tags[k]] if k in _tags else (default if default is not None else None)
    )
    mock_file.__bool__ = lambda self: True
    mock_file.__iter__ = lambda self: iter(_tags)

    return mock_file


# ══════════════════════════════════════════════════════════════════════
#  PptxReaderTool
# ══════════════════════════════════════════════════════════════════════

class TestPptxReaderSpec:
    def setup_method(self):
        self.tool = PptxReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "pptx_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_parameters_present(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters
        assert "slide" in self.tool.spec.parameters


class TestPptxReaderValidation:
    def setup_method(self):
        self.tool = PptxReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "x.pptx"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_wrong_extension(self, tmp_path):
        f = tmp_path / "file.ppt"
        f.write_bytes(b"fake")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert ".pptx" in r.error

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([["Текст"]])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f), action="fly")
        assert r.success is False
        assert "fly" in r.error


class TestPptxReaderExtractText:
    def setup_method(self):
        self.tool = PptxReaderTool()

    def test_extract_all_slides(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([
            ["Заголовок 1", "Контент слайда один"],
            ["Заголовок 2", "Контент слайда два"],
        ])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True
        assert "Заголовок 1" in r.output
        assert "Контент слайда два" in r.output
        assert r.metadata["slides_total"] == 2

    def test_extract_single_slide(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([
            ["Первый слайд"],
            ["Второй слайд"],
        ])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f), slide=2)
        assert r.success is True
        assert "Второй слайд" in r.output
        assert "Первый слайд" not in r.output

    def test_truncation(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        long_text = "X" * 5000
        mock_prs = _make_pptx_mock([[long_text]])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f), max_chars=100)
        assert r.success is True
        assert len(r.output) == 100
        assert r.metadata["truncated"] is True

    def test_empty_presentation(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True
        assert r.output == ""


class TestPptxReaderMetadata:
    def setup_method(self):
        self.tool = PptxReaderTool()

    def test_get_metadata(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([["Слайд 1"], ["Слайд 2"]])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f), action="get_metadata")
        assert r.success is True
        assert r.output["title"] == "Тестовая Презентация"
        assert r.output["author"] == "Тестер"
        assert r.output["slides"] == 2
        assert "slide_width" in r.output
        assert "file_size_kb" in r.output

    def test_list_slides(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        mock_prs = _make_pptx_mock([
            ["Заголовок Первый", "контент"],
            ["Заголовок Второй"],
        ])
        with patch("pptx.Presentation", return_value=mock_prs):
            r = self.tool.execute(file_path=str(f), action="list_slides")
        assert r.success is True
        assert isinstance(r.output, list)
        assert len(r.output) == 2
        assert r.output[0]["slide"] == 1
        assert r.metadata["total_slides"] == 2

    def test_exception_returns_fail(self, tmp_path):
        f = tmp_path / "p.pptx"
        f.write_bytes(b"PK fake")
        with patch("pptx.Presentation", side_effect=Exception("corrupt")):
            r = self.tool.execute(file_path=str(f))
        assert r.success is False


# ══════════════════════════════════════════════════════════════════════
#  OdtReaderTool
# ══════════════════════════════════════════════════════════════════════

class TestOdtReaderSpec:
    def setup_method(self):
        self.tool = OdtReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "odt_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_parameters_present(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters


class TestOdtReaderValidation:
    def setup_method(self):
        self.tool = OdtReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "x.odt"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_wrong_extension(self, tmp_path):
        f = tmp_path / "file.doc"
        f.write_bytes(b"fake")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert "Неподдерживаемый" in r.error or "doc" in r.error.lower()

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "doc.odt"
        f.write_bytes(b"PK fake odt")

        mock_doc = MagicMock()
        with patch("odf.opendocument.load", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f), action="fly")
        assert r.success is False
        assert "fly" in r.error

    def test_ods_accepted(self, tmp_path):
        """Файлы .ods тоже принимаются."""
        f = tmp_path / "table.ods"
        f.write_bytes(b"PK fake ods")
        mock_doc = MagicMock()
        mock_doc.body = None
        with patch("odf.opendocument.load", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True

    def test_odp_accepted(self, tmp_path):
        """Файлы .odp тоже принимаются."""
        f = tmp_path / "pres.odp"
        f.write_bytes(b"PK fake odp")
        mock_doc = MagicMock()
        mock_doc.body = None
        with patch("odf.opendocument.load", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True


class TestOdtReaderExtractText:
    def setup_method(self):
        self.tool = OdtReaderTool()

    def _make_odt_body(self, paragraphs: list[str]) -> object:
        """
        Создаёт объект body с параграфами, точно имитирующий ODF-структуру.
        Использует реальные классы вместо MagicMock, чтобы hasattr("data")
        работал правильно (не перехватывался MagicMock-автогенерацией).
        """
        class TextNode:
            """ODF text-node: имеет data но не имеет qname."""
            def __init__(self, text: str) -> None:
                self.data = text
                self.childNodes: list = []

        class ParaNode:
            """ODF paragraph node (p/h): имеет qname + childNodes, но не data."""
            def __init__(self, text: str) -> None:
                self.qname = ("urn:oasis:names:tc:opendocument:xmlns:text:1.0", "p")
                self.childNodes = [TextNode(text)]

        class Body:
            def __init__(self, paras: list) -> None:
                self.childNodes = paras

        return Body([ParaNode(t) for t in paragraphs])

    def test_extract_text_basic(self, tmp_path):
        f = tmp_path / "doc.odt"
        f.write_bytes(b"PK fake")

        body = self._make_odt_body(["Первый параграф", "Второй параграф"])
        mock_doc = MagicMock()
        mock_doc.body = body

        with patch("odf.opendocument.load", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert r.metadata["file_type"] == ".odt"

    def test_extract_no_body(self, tmp_path):
        f = tmp_path / "doc.odt"
        f.write_bytes(b"PK fake")

        mock_doc = MagicMock()
        mock_doc.body = None

        with patch("odf.opendocument.load", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert r.output == ""

    def test_exception_returns_fail(self, tmp_path):
        f = tmp_path / "doc.odt"
        f.write_bytes(b"PK fake")

        with patch("odf.opendocument.load", side_effect=Exception("bad zip")):
            r = self.tool.execute(file_path=str(f))

        assert r.success is False
        assert r.error is not None


# ══════════════════════════════════════════════════════════════════════
#  MediaReaderTool
# ══════════════════════════════════════════════════════════════════════

class TestMediaReaderSpec:
    def setup_method(self):
        self.tool = MediaReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "media_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_parameters_present(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters


class TestMediaReaderValidation:
    def setup_method(self):
        self.tool = MediaReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "song.mp3"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "video.rm"
        f.write_bytes(b"fake")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert "Неподдерживаемый" in r.error or "rm" in r.error.lower()

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3 fake mp3")
        mock_file = _make_mutagen_mock()
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f), action="play")
        assert r.success is False
        assert "play" in r.error

    def test_mp4_extension_accepted(self, tmp_path):
        f = tmp_path / "vid.mp4"
        f.write_bytes(b"fake mp4")
        mock_file = _make_mutagen_mock()
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True

    def test_flac_extension_accepted(self, tmp_path):
        f = tmp_path / "track.flac"
        f.write_bytes(b"fLaC fake")
        mock_file = _make_mutagen_mock()
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))
        assert r.success is True


class TestMediaReaderGetMetadata:
    def setup_method(self):
        self.tool = MediaReaderTool()

    def test_metadata_basic(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3 fake")

        mock_file = _make_mutagen_mock(
            duration=240.5,
            bitrate=192_000,
            sample_rate=44100,
            channels=2,
            tags={"title": "Тест", "artist": "Исполнитель"},
        )

        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert r.output["duration_sec"] == 240.5
        assert r.output["duration_str"] == "04:00"
        assert r.output["bitrate_kbps"] == 192.0
        assert r.output["sample_rate_hz"] == 44100
        assert r.output["channels"] == 2
        assert r.output["channels_str"] == "Stereo"
        assert r.output["type"] == "audio"
        assert r.output["title"] == "Тест"
        assert r.output["artist"] == "Исполнитель"

    def test_metadata_video(self, tmp_path):
        f = tmp_path / "movie.mp4"
        f.write_bytes(b"fake mp4")

        mock_file = _make_mutagen_mock(duration=5400.0)

        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert r.output["type"] == "video"
        assert r.output["duration_str"] == "01:30:00"

    def test_metadata_mono(self, tmp_path):
        f = tmp_path / "voice.mp3"
        f.write_bytes(b"ID3 fake")

        mock_file = _make_mutagen_mock(channels=1)
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))

        assert r.output["channels_str"] == "Mono"

    def test_duration_in_metadata(self, tmp_path):
        f = tmp_path / "s.mp3"
        f.write_bytes(b"ID3 fake")
        mock_file = _make_mutagen_mock()
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f))
        assert "duration_ms" in r.metadata


class TestMediaReaderListTags:
    def setup_method(self):
        self.tool = MediaReaderTool()

    def test_list_tags_with_id3(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3 fake")

        # Мокируем ID3-тег
        tag_title = MagicMock()
        tag_title.text = ["My Song"]

        tag_artist = MagicMock()
        tag_artist.text = ["My Artist"]

        mock_tags = {"TIT2": tag_title, "TPE1": tag_artist}

        mock_file_easy = _make_mutagen_mock()
        mock_file_raw = _make_mutagen_mock()
        mock_file_raw.tags = mock_tags

        with patch("mutagen.File", side_effect=[mock_file_easy, mock_file_raw]):
            r = self.tool.execute(file_path=str(f), action="list_tags")

        assert r.success is True
        assert "TIT2" in r.output or "TPE1" in r.output

    def test_list_tags_empty(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3 fake")

        mock_file_easy = _make_mutagen_mock()
        mock_file_raw = _make_mutagen_mock()
        mock_file_raw.tags = None

        with patch("mutagen.File", side_effect=[mock_file_easy, mock_file_raw]):
            r = self.tool.execute(file_path=str(f), action="list_tags")

        assert r.success is True
        assert r.output == {}
        assert "Теги отсутствуют" in r.metadata.get("note", "")


class TestMediaReaderDescribe:
    def setup_method(self):
        self.tool = MediaReaderTool()

    def test_describe_returns_string(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3 fake")
        mock_file = _make_mutagen_mock(
            duration=180.0,
            tags={"title": "Полёт над гнездом", "artist": "Группа"},
        )
        with patch("mutagen.File", return_value=mock_file):
            r = self.tool.execute(file_path=str(f), action="describe")
        assert r.success is True
        assert isinstance(r.output, str)
        assert "song.mp3" in r.output
        assert "03:00" in r.output

    def test_describe_none_mutagen_file(self, tmp_path):
        """Если mutagen не смог распознать — не должен падать."""
        f = tmp_path / "s.mp3"
        f.write_bytes(b"ID3 fake")
        with patch("mutagen.File", return_value=None):
            r = self.tool.execute(file_path=str(f), action="describe")
        assert r.success is True
        assert "MP3" in r.output

    def test_exception_returns_fail(self, tmp_path):
        f = tmp_path / "s.mp3"
        f.write_bytes(b"ID3 fake")
        with patch("mutagen.File", side_effect=Exception("read error")):
            r = self.tool.execute(file_path=str(f))
        assert r.success is False
