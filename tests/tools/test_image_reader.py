"""tests/tools/test_image_reader.py — тесты ImageReaderTool"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from tools.builtins.image_reader import ImageReaderTool


class TestImageReaderSpec:
    def setup_method(self):
        self.tool = ImageReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "image_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_parameters_present(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters
        assert "lang" in self.tool.spec.parameters


class TestImageReaderValidation:
    def setup_method(self):
        self.tool = ImageReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "x.png"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "file.bmp2"
        f.write_bytes(b"fake")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert "bmp2" in r.error or "неподдерживаемый" in r.error.lower()

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        mock_img = MagicMock()
        mock_img.size = (100, 100)
        mock_img.format = "PNG"
        mock_img.mode = "RGB"
        mock_img.info = {}

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f), action="fly")

        assert r.success is False
        assert "fly" in r.error


class TestImageReaderGetMetadata:
    def setup_method(self):
        self.tool = ImageReaderTool()

    def _make_mock_image(self, width=800, height=600, fmt="JPEG", mode="RGB"):
        mock_img = MagicMock()
        mock_img.size = (width, height)
        mock_img.format = fmt
        mock_img.mode = mode
        mock_img.info = {}
        # _getexif raises AttributeError for non-JPEG (PNG, etc.)
        mock_img._getexif.side_effect = AttributeError
        return mock_img

    def test_metadata_basic(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff fake jpeg")

        mock_img = self._make_mock_image(1920, 1080, "JPEG")

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f), action="get_metadata")

        assert r.success is True
        assert r.output["width_px"] == 1920
        assert r.output["height_px"] == 1080
        assert r.output["format"] == "JPEG"
        assert r.output["mode"] == "RGB"
        assert r.output["megapixels"] == pytest.approx(2.0736, abs=0.01)

    def test_metadata_default_action(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        mock_img = self._make_mock_image(100, 100, "PNG")

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f))  # default action

        assert r.success is True
        assert "width_px" in r.output

    def test_metadata_has_file_size(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"A" * 1024)  # 1 КБ

        mock_img = self._make_mock_image()

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f))

        assert "file_size_kb" in r.output
        assert r.output["file_size_kb"] == pytest.approx(1.0, abs=0.1)

    def test_metadata_duration(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        mock_img = self._make_mock_image()

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f))

        assert "duration_ms" in r.metadata
        assert r.metadata["duration_ms"] >= 0


class TestImageReaderDescribe:
    def setup_method(self):
        self.tool = ImageReaderTool()

    def test_describe_basic(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.write_bytes(b"JPEG fake")

        # Мокируем изображение с RGB-пикселями (светлые)
        mock_img = MagicMock()
        mock_img.size = (200, 150)
        mock_img.format = "JPEG"
        mock_img.mode = "RGB"
        mock_img.info = {}

        # Мокируем конвертацию + resize + getdata
        bright_pixels = [(220, 220, 220)] * 10000
        mock_small = MagicMock()
        mock_small.getdata.return_value = bright_pixels

        mock_rgb = MagicMock()
        mock_rgb.resize.return_value = mock_small

        mock_img.convert.return_value = mock_rgb

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f), action="describe")

        assert r.success is True
        assert "200" in r.output
        assert "150" in r.output
        assert isinstance(r.output, str)
        assert len(r.output) > 20

    def test_describe_contains_format(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        mock_img = MagicMock()
        mock_img.size = (400, 300)
        mock_img.format = "PNG"
        mock_img.mode = "RGBA"
        mock_img.info = {}

        dark_pixels = [(20, 20, 20)] * 10000
        mock_small = MagicMock()
        mock_small.getdata.return_value = dark_pixels

        mock_rgb = MagicMock()
        mock_rgb.resize.return_value = mock_small

        mock_img.convert.return_value = mock_rgb

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f), action="describe")

        assert r.success is True
        assert "PNG" in r.output

    def test_describe_dimension_in_metadata(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        mock_img = MagicMock()
        mock_img.size = (1024, 768)
        mock_img.format = "PNG"
        mock_img.mode = "RGB"
        mock_img.info = {}

        pixels = [(100, 150, 200)] * 10000
        mock_small = MagicMock()
        mock_small.getdata.return_value = pixels
        mock_rgb = MagicMock()
        mock_rgb.resize.return_value = mock_small
        mock_img.convert.return_value = mock_rgb

        with patch("PIL.Image.open", return_value=mock_img):
            r = self.tool.execute(file_path=str(f), action="describe")

        assert r.metadata.get("width") == 1024
        assert r.metadata.get("height") == 768


class TestImageReaderOCR:
    def setup_method(self):
        self.tool = ImageReaderTool()

    def test_extract_text_success(self, tmp_path):
        f = tmp_path / "scan.png"
        f.write_bytes(b"PNG fake")

        mock_img = MagicMock()
        mock_img.size = (800, 600)
        mock_img.format = "PNG"
        mock_img.mode = "RGB"
        mock_img.info = {}

        mock_img.convert.return_value = mock_img

        with patch("PIL.Image.open", return_value=mock_img):
            with patch("pytesseract.image_to_string", return_value="Извлечённый текст\n"):
                r = self.tool.execute(file_path=str(f), action="extract_text")

        assert r.success is True
        assert r.output == "Извлечённый текст"
        assert r.metadata["lang"] == "rus+eng"
        assert r.metadata["chars"] == len("Извлечённый текст")

    def test_extract_text_custom_lang(self, tmp_path):
        f = tmp_path / "scan.png"
        f.write_bytes(b"PNG fake")

        mock_img = MagicMock()
        mock_img.size = (100, 100)
        mock_img.format = "PNG"
        mock_img.mode = "L"
        mock_img.info = {}

        with patch("PIL.Image.open", return_value=mock_img):
            with patch("pytesseract.image_to_string", return_value="Hello") as mock_tess:
                r = self.tool.execute(file_path=str(f), action="extract_text", lang="eng")

        assert r.success is True
        assert r.metadata["lang"] == "eng"
        mock_tess.assert_called_once_with(mock_img, lang="eng")

    def test_extract_text_no_pytesseract(self, tmp_path):
        f = tmp_path / "scan.jpg"
        f.write_bytes(b"JPEG fake")

        mock_img = MagicMock()
        mock_img.size = (100, 100)
        mock_img.format = "JPEG"
        mock_img.mode = "RGB"
        mock_img.info = {}
        mock_img.convert.return_value = mock_img

        with patch("PIL.Image.open", return_value=mock_img):
            with patch.dict("sys.modules", {"pytesseract": None}):
                # Имитируем ImportError
                import builtins
                real_import = builtins.__import__

                def mock_import(name, *args, **kwargs):
                    if name == "pytesseract":
                        raise ImportError("No module named 'pytesseract'")
                    return real_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    r = self.tool.execute(file_path=str(f), action="extract_text")

        assert r.success is False
        assert "pytesseract" in r.error.lower()

    def test_exception_returns_fail(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG fake")

        with patch("PIL.Image.open", side_effect=OSError("cannot open")):
            r = self.tool.execute(file_path=str(f))

        assert r.success is False
        assert r.error is not None
