"""tests/tools/test_office_reader.py — тесты DocxReaderTool и XlsxReaderTool"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from tools.builtins.office_reader import DocxReaderTool, XlsxReaderTool


# ══════════════════════════════════════════════════════════════════════
#  DocxReaderTool
# ══════════════════════════════════════════════════════════════════════

class TestDocxReaderSpec:
    def setup_method(self):
        self.tool = DocxReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "docx_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_parameters_present(self):
        assert "file_path" in self.tool.spec.parameters
        assert "action" in self.tool.spec.parameters


class TestDocxReaderValidation:
    def setup_method(self):
        self.tool = DocxReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False
        assert "file_path" in r.error.lower()

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "x.docx"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_wrong_extension(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert ".docx" in r.error

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake docx")

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f), action="fly")

        assert r.success is False
        assert "fly" in r.error


class TestDocxReaderExtractText:
    def setup_method(self):
        self.tool = DocxReaderTool()

    def _make_paragraph(self, text: str, style_name: str = "Normal") -> MagicMock:
        para = MagicMock()
        para.text = text
        para.style.name = style_name
        return para

    def test_extract_text_basic(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            self._make_paragraph("Привет, мир!"),
            self._make_paragraph("Второй параграф"),
        ]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert "Привет, мир!" in r.output
        assert "Второй параграф" in r.output

    def test_extract_text_with_tables(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        cell1, cell2 = MagicMock(), MagicMock()
        cell1.text = "Имя"
        cell2.text = "Возраст"
        row = MagicMock()
        row.cells = [cell1, cell2]
        table = MagicMock()
        table.rows = [row]

        mock_doc = MagicMock()
        mock_doc.paragraphs = [self._make_paragraph("Заголовок")]
        mock_doc.tables = [table]

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert "Имя" in r.output
        assert "Возраст" in r.output
        assert "Таблица 1" in r.output

    def test_text_truncation(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        long_text = "X" * 5000
        mock_doc = MagicMock()
        mock_doc.paragraphs = [self._make_paragraph(long_text)]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f), max_chars=100)

        assert r.success is True
        assert len(r.output) == 100
        assert r.metadata["truncated"] is True

    def test_empty_paragraphs_skipped(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            self._make_paragraph(""),   # пустой — должен быть пропущен
            self._make_paragraph("   "),  # пустой пробельный
            self._make_paragraph("Нормальный текст"),
        ]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert "Нормальный текст" in r.output


class TestDocxReaderMetadata:
    def setup_method(self):
        self.tool = DocxReaderTool()

    def test_get_metadata(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        mock_props = MagicMock()
        mock_props.title = "Годовой отчёт"
        mock_props.author = "Петров"
        mock_props.subject = "Финансы"
        mock_props.created = None
        mock_props.modified = None

        mock_doc = MagicMock()
        mock_doc.core_properties = mock_props
        mock_doc.paragraphs = [MagicMock(), MagicMock()]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f), action="get_metadata")

        assert r.success is True
        assert r.output["title"] == "Годовой отчёт"
        assert r.output["author"] == "Петров"
        assert r.output["paragraphs"] == 2

    def test_list_headings(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK fake")

        def _para(text, style):
            p = MagicMock()
            p.text = text
            p.style.name = style
            return p

        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            _para("Глава 1", "Heading 1"),
            _para("Обычный текст", "Normal"),
            _para("Раздел 1.1", "Heading 2"),
        ]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            r = self.tool.execute(file_path=str(f), action="list_headings")

        assert r.success is True
        assert isinstance(r.output, list)
        assert len(r.output) == 2
        assert r.output[0]["text"] == "Глава 1"
        assert r.output[0]["level"] == 1
        assert r.output[1]["text"] == "Раздел 1.1"
        assert r.output[1]["level"] == 2


# ══════════════════════════════════════════════════════════════════════
#  XlsxReaderTool
# ══════════════════════════════════════════════════════════════════════

class TestXlsxReaderSpec:
    def setup_method(self):
        self.tool = XlsxReaderTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "xlsx_reader"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_parameters(self):
        assert "file_path" in self.tool.spec.parameters
        assert "sheet" in self.tool.spec.parameters
        assert "max_rows" in self.tool.spec.parameters


class TestXlsxReaderValidation:
    def setup_method(self):
        self.tool = XlsxReaderTool()

    def test_no_file_path(self):
        r = self.tool.execute()
        assert r.success is False

    def test_file_not_found(self, tmp_path):
        r = self.tool.execute(file_path=str(tmp_path / "x.xlsx"))
        assert r.success is False
        assert "не найден" in r.error.lower()

    def test_wrong_extension(self, tmp_path):
        f = tmp_path / "file.csv"
        f.write_text("a,b,c")
        r = self.tool.execute(file_path=str(f))
        assert r.success is False
        assert ".xlsx" in r.error

    def test_unknown_sheet(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake xlsx")

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f), action="extract_data", sheet="NoSuchSheet")

        assert r.success is False
        assert "NoSuchSheet" in r.error


def _make_xlsx_mock(rows: list[list]) -> MagicMock:
    """Создаёт мок workbook с данными."""
    mock_cells_rows = []
    for row_data in rows:
        row_mocks = []
        for val in row_data:
            cell = MagicMock()
            cell.value = val
            row_mocks.append(cell)
        mock_cells_rows.append(row_mocks)

    mock_ws = MagicMock()
    mock_ws.title = "Sheet1"
    mock_ws.max_row = len(rows)
    mock_ws.iter_rows = MagicMock(return_value=iter(mock_cells_rows))

    mock_wb = MagicMock()
    mock_wb.active = mock_ws
    mock_wb.sheetnames = ["Sheet1"]
    return mock_wb


class TestXlsxReaderExtractData:
    def setup_method(self):
        self.tool = XlsxReaderTool()

    def test_extract_basic_data(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_wb = _make_xlsx_mock([
            ["Имя", "Возраст", "Город"],
            ["Иван", 25, "Москва"],
            ["Мария", 30, "СПб"],
        ])

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert "Имя" in r.output
        assert "Иван" in r.output
        assert "Мария" in r.output
        assert "|" in r.output  # CSV-стиль

    def test_empty_rows_skipped(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_wb = _make_xlsx_mock([
            ["Данные"],
            [None, None],  # пустая строка
            ["Конец"],
        ])

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f))

        assert r.success is True
        assert "Данные" in r.output
        assert "Конец" in r.output

    def test_get_sheets(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Продажи", "Расходы", "Сводка"]

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f), action="get_sheets")

        assert r.success is True
        assert r.output == ["Продажи", "Расходы", "Сводка"]

    def test_get_metadata(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_props = MagicMock()
        mock_props.title = "Отчёт 2024"
        mock_props.creator = "Сидоров"
        mock_props.description = ""
        mock_props.created = None
        mock_props.modified = None

        mock_wb = MagicMock()
        mock_wb.properties = mock_props
        mock_wb.sheetnames = ["Лист1", "Лист2"]

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f), action="get_metadata")

        assert r.success is True
        assert r.output["title"] == "Отчёт 2024"
        assert r.output["sheet_count"] == 2
        assert r.output["sheets"] == ["Лист1", "Лист2"]

    def test_unknown_action(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f), action="bad_action")

        assert r.success is False
        assert "bad_action" in r.error

    def test_exception_returns_fail(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        with patch("openpyxl.load_workbook", side_effect=RuntimeError("broken")):
            r = self.tool.execute(file_path=str(f))

        assert r.success is False
        assert r.error is not None

    def test_duration_in_metadata(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK fake")

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["S1"]

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            r = self.tool.execute(file_path=str(f), action="get_sheets")

        assert "duration_ms" in r.metadata
