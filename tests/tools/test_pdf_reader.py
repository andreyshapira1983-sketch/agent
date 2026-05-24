"""tests/tools/test_pdf_reader.py — тесты PdfReaderTool

Используем unittest.mock для изоляции от реальных файлов.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.builtins.pdf_reader import PdfReaderTool


class TestPdfReaderToolSpec:
    def setup_method(self):
        self.tool = PdfReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "pdf_reader"

    def test_spec_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_spec_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_spec_has_description(self):
        assert len(self.tool.spec.description) > 10

    def test_spec_has_parameters(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters


class TestPdfReaderValidation:
    def setup_method(self):
        self.tool = PdfReaderTool()

    def test_missing_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_empty_file_path(self):
        r = self.tool.execute(file_path="")
        assert r.success is False

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "nonexistent.pdf"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_wrong_extension(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert ".pdf" in r.error

    def test_unknown_action(self, tmp_path):
        # Создаём фейковый PDF (заглушка для проверки импорта)
        f = tmp_path / "test.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="invalid_action")

        assert r.success is False
        assert "invalid_action" in r.error


class TestPdfReaderExtractText:
    def setup_method(self):
        self.tool = PdfReaderTool()

    def test_extract_text_success(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Первая страница"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Вторая страница"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, mock_page2]
        mock_reader.metadata = None

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="extract_text")

        assert r.success is True
        assert "Первая страница" in r.output
        assert "Вторая страница" in r.output
        assert r.metadata["pages"] == 2

    def test_extract_text_single_page(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Страница один"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page, MagicMock()]
        mock_reader.pages[1].extract_text.return_value = "Страница два"

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="extract_text", page=1)

        assert r.success is True
        assert "Страница один" in r.output
        assert "Страница два" not in r.output

    def test_extract_text_page_out_of_range(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="extract_text", page=99)

        assert r.success is False
        assert "99" in r.error

    def test_extract_text_truncation(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        long_text = "A" * 50_000

        mock_page = MagicMock()
        mock_page.extract_text.return_value = long_text

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="extract_text", max_chars=1000)

        assert r.success is True
        assert len(r.output) == 1000
        assert r.metadata["truncated"] is True

    def test_extract_text_default_action(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Текст"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f))  # action не передан

        assert r.success is True


class TestPdfReaderMetadata:
    def setup_method(self):
        self.tool = PdfReaderTool()

    def test_get_metadata(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_meta = {"/Title": "Отчёт", "/Author": "Иванов", "/Subject": "Финансы"}
        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock(), MagicMock()]
        mock_reader.metadata = mock_meta

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="get_metadata")

        assert r.success is True
        assert r.output["title"] == "Отчёт"
        assert r.output["author"] == "Иванов"
        assert r.output["pages"] == 2
        assert "file_size_kb" in r.output

    def test_get_page_count(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()] * 7

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="get_page_count")

        assert r.success is True
        assert r.output == 7

    def test_metadata_no_exif(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()]
        mock_reader.metadata = None  # метаданные отсутствуют

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="get_metadata")

        assert r.success is True
        assert r.output["title"] == ""
        assert r.output["author"] == ""

    def test_duration_in_metadata(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            r = self.tool.execute(file_path=str(f), action="get_page_count")

        assert "duration_ms" in r.metadata
        assert r.metadata["duration_ms"] >= 0


class TestPdfReaderErrors:
    def setup_method(self):
        self.tool = PdfReaderTool()

    def test_corrupted_pdf(self, tmp_path):
        f = tmp_path / "bad.pdf"
        f.write_bytes(b"not a real pdf content")

        with patch("pypdf.PdfReader", side_effect=Exception("invalid PDF")):
            r = self.tool.execute(file_path=str(f))

        assert r.success is False
        assert "ошибка" in r.error.lower()

    def test_returns_toolresult_not_exception(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch("pypdf.PdfReader", side_effect=RuntimeError("boom")):
            r = self.tool.execute(file_path=str(f))

        # Никогда не бросаем — всегда ToolResult
        assert r is not None
        assert r.success is False
