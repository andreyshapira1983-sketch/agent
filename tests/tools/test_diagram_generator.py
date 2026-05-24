"""tests/tools/test_diagram_generator.py — тесты DiagramGeneratorTool"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.builtins.diagram_generator import (
    DiagramGeneratorTool,
    _build_flowchart_svg,
    _mermaid_html_wrapper,
    _plantuml_online_url,
    _simple_text_svg,
    _svg_escape,
)


class TestDiagramGeneratorSpec:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "diagram_generator"

    def test_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_not_requires_approval(self):
        assert self.tool.spec.requires_approval is False

    def test_parameters(self):
        assert "diagram_type" in self.tool.spec.parameters
        assert "content" in self.tool.spec.parameters
        assert "output_file" in self.tool.spec.parameters


class TestDiagramGeneratorValidation:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_no_diagram_type(self):
        r = self.tool.execute(content="flowchart TD\n A-->B")
        assert r.success is False
        assert "diagram_type" in r.error.lower()

    def test_no_content(self):
        r = self.tool.execute(diagram_type="mermaid")
        assert r.success is False
        assert "content" in r.error.lower()

    def test_unknown_type(self):
        r = self.tool.execute(diagram_type="autocad", content="some content")
        assert r.success is False
        assert "autocad" in r.error

    def test_content_too_long(self):
        r = self.tool.execute(
            diagram_type="mermaid",
            content="A" * 60_000,
        )
        assert r.success is False
        assert "длинное" in r.error.lower() or "max" in r.error.lower()


# ══════════════════════════════════════════════════════════════════════
#  Mermaid
# ══════════════════════════════════════════════════════════════════════

class TestMermaidGeneration:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_mermaid_creates_file(self, tmp_path):
        out = tmp_path / "flow.mmd"
        code = "flowchart TD\n    A[Начало] --> B[Конец]"

        r = self.tool.execute(
            diagram_type="mermaid",
            content=code,
            output_file=str(out),
        )

        assert r.success is True
        assert out.exists()
        assert out.read_text(encoding="utf-8") == code

    def test_mermaid_creates_html_preview(self, tmp_path):
        out = tmp_path / "diagram.mmd"
        code = "flowchart LR\n    A --> B --> C"

        r = self.tool.execute(
            diagram_type="mermaid",
            content=code,
            output_file=str(out),
        )

        assert r.success is True
        html = tmp_path / "diagram.html"
        assert html.exists()
        content_html = html.read_text(encoding="utf-8")
        assert "mermaid" in content_html
        assert "flowchart" in content_html

    def test_mermaid_auto_extension(self, tmp_path):
        out = tmp_path / "diagram.xyz"
        r = self.tool.execute(
            diagram_type="mermaid",
            content="sequenceDiagram\n    A->>B: Hello",
            output_file=str(out),
        )
        assert r.success is True
        assert (tmp_path / "diagram.mmd").exists()

    def test_mermaid_output_mentions_github(self, tmp_path):
        out = tmp_path / "d.mmd"
        r = self.tool.execute(
            diagram_type="mermaid",
            content="flowchart TD\n A-->B",
            output_file=str(out),
        )
        assert r.success is True
        assert "GitHub" in r.output or "mermaid.live" in r.output

    def test_mermaid_default_filename(self, tmp_path):
        import os
        os.chdir(tmp_path)
        r = self.tool.execute(
            diagram_type="mermaid",
            content="flowchart TD\n A-->B",
        )
        assert r.success is True
        assert (tmp_path / "diagram.mmd").exists()


# ══════════════════════════════════════════════════════════════════════
#  Graphviz
# ══════════════════════════════════════════════════════════════════════

class TestGraphvizGeneration:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_graphviz_saves_dot_file(self, tmp_path):
        out = tmp_path / "graph.dot"
        dot = "digraph G {\n    A -> B;\n    B -> C;\n}"

        r = self.tool.execute(
            diagram_type="graphviz",
            content=dot,
            output_file=str(out),
            render=False,
        )

        assert r.success is True
        assert out.exists()
        assert out.read_text(encoding="utf-8") == dot

    def test_graphviz_auto_extension(self, tmp_path):
        out = tmp_path / "g.xyz"
        r = self.tool.execute(
            diagram_type="graphviz",
            content="digraph G { A -> B; }",
            output_file=str(out),
            render=False,
        )
        assert r.success is True
        assert (tmp_path / "g.dot").exists()

    def test_graphviz_render_failure_still_saves_dot(self, tmp_path):
        out = tmp_path / "graph.dot"

        with patch("graphviz.Source") as mock_src_cls:
            mock_src = MagicMock()
            mock_src.render.side_effect = Exception("Graphviz binary not found")
            mock_src_cls.return_value = mock_src

            r = self.tool.execute(
                diagram_type="graphviz",
                content="digraph G { A -> B; }",
                output_file=str(out),
                render=True,
            )

        assert r.success is True  # DOT-файл сохранён — успех
        assert out.exists()
        assert "не удался" in r.output or "ошибка" in r.output.lower() or "dreampuf" in r.output

    def test_graphviz_no_package_still_saves_dot(self, tmp_path):
        out = tmp_path / "graph.dot"

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "graphviz":
                raise ImportError("No module named 'graphviz'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            r = self.tool.execute(
                diagram_type="graphviz",
                content="digraph G { A -> B; }",
                output_file=str(out),
                render=True,
            )

        assert r.success is True
        assert out.exists()

    def test_graphviz_output_contains_online_link(self, tmp_path):
        out = tmp_path / "g.dot"
        r = self.tool.execute(
            diagram_type="graphviz",
            content="digraph G { A -> B; }",
            output_file=str(out),
            render=False,
        )
        assert r.success is True
        assert "dreampuf" in r.output or "viz-js" in r.output or "graphviz" in r.output.lower()


# ══════════════════════════════════════════════════════════════════════
#  SVG
# ══════════════════════════════════════════════════════════════════════

class TestSvgGeneration:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_svg_passthrough(self, tmp_path):
        """Если передан готовый SVG — сохраняем как есть."""
        out = tmp_path / "direct.svg"
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'

        r = self.tool.execute(
            diagram_type="svg",
            content=svg,
            output_file=str(out),
        )

        assert r.success is True
        assert out.exists()
        assert svg in out.read_text(encoding="utf-8")

    def test_svg_from_json_spec(self, tmp_path):
        out = tmp_path / "flowchart.svg"
        spec = {
            "nodes": [
                {"id": "A", "label": "Начало", "shape": "ellipse"},
                {"id": "B", "label": "Процесс", "shape": "rect"},
                {"id": "C", "label": "Конец", "shape": "ellipse"},
            ],
            "edges": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C", "label": "готово"},
            ],
        }

        r = self.tool.execute(
            diagram_type="svg",
            content=json.dumps(spec),
            output_file=str(out),
            title="Тест",
        )

        assert r.success is True
        assert out.exists()
        svg_text = out.read_text(encoding="utf-8")
        assert "<svg" in svg_text
        assert "Начало" in svg_text
        assert "Процесс" in svg_text
        assert "Конец" in svg_text
        assert r.metadata["nodes"] == 3
        assert r.metadata["edges"] == 2

    def test_svg_from_json_with_diamond(self, tmp_path):
        out = tmp_path / "d.svg"
        spec = {
            "nodes": [{"id": "X", "label": "Решение?", "shape": "diamond"}],
            "edges": [],
        }
        r = self.tool.execute(
            diagram_type="svg",
            content=json.dumps(spec),
            output_file=str(out),
        )
        assert r.success is True
        svg = out.read_text(encoding="utf-8")
        assert "polygon" in svg  # diamond рендерится как polygon

    def test_svg_fallback_for_plain_text(self, tmp_path):
        out = tmp_path / "text.svg"
        r = self.tool.execute(
            diagram_type="svg",
            content="Не JSON и не SVG — просто текст",
            output_file=str(out),
        )
        assert r.success is True
        assert out.exists()
        svg = out.read_text(encoding="utf-8")
        assert "<svg" in svg

    def test_svg_auto_extension(self, tmp_path):
        out = tmp_path / "diagram.xyz"
        r = self.tool.execute(
            diagram_type="svg",
            content='<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            output_file=str(out),
        )
        assert r.success is True
        assert (tmp_path / "diagram.svg").exists()


# ══════════════════════════════════════════════════════════════════════
#  PlantUML
# ══════════════════════════════════════════════════════════════════════

class TestPlantUmlGeneration:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_plantuml_saves_file(self, tmp_path):
        out = tmp_path / "diagram.puml"
        content = "@startuml\nA -> B: Hello\n@enduml"

        r = self.tool.execute(
            diagram_type="plantuml",
            content=content,
            output_file=str(out),
        )

        assert r.success is True
        assert out.exists()
        assert "@startuml" in out.read_text(encoding="utf-8")

    def test_plantuml_adds_wrapper_if_missing(self, tmp_path):
        out = tmp_path / "d.puml"
        content = "A -> B: Message"  # без @startuml

        r = self.tool.execute(
            diagram_type="plantuml",
            content=content,
            output_file=str(out),
        )

        assert r.success is True
        saved = out.read_text(encoding="utf-8")
        assert "@startuml" in saved
        assert "@enduml" in saved

    def test_plantuml_auto_extension(self, tmp_path):
        out = tmp_path / "d.xyz"
        r = self.tool.execute(
            diagram_type="plantuml",
            content="A -> B",
            output_file=str(out),
        )
        assert r.success is True
        assert (tmp_path / "d.puml").exists()

    def test_plantuml_output_contains_online_info(self, tmp_path):
        out = tmp_path / "d.puml"
        r = self.tool.execute(
            diagram_type="plantuml",
            content="A -> B: test",
            output_file=str(out),
        )
        assert r.success is True
        assert "plantuml" in r.output.lower()

    def test_plantuml_has_online_url(self, tmp_path):
        out = tmp_path / "d.puml"
        r = self.tool.execute(
            diagram_type="plantuml",
            content="@startuml\nA -> B\n@enduml",
            output_file=str(out),
        )
        assert r.success is True
        # online_url может быть пустой если zlib недоступен — но не должен сломаться
        url = r.metadata.get("online_url", "")
        assert isinstance(url, str)


# ══════════════════════════════════════════════════════════════════════
#  Вспомогательные функции
# ══════════════════════════════════════════════════════════════════════

class TestHelperFunctions:
    def test_svg_escape_ampersand(self):
        assert _svg_escape("a & b") == "a &amp; b"

    def test_svg_escape_lt_gt(self):
        assert _svg_escape("<tag>") == "&lt;tag&gt;"

    def test_svg_escape_quote(self):
        assert _svg_escape('"hello"') == "&quot;hello&quot;"

    def test_svg_escape_clean_string(self):
        assert _svg_escape("clean text") == "clean text"

    def test_mermaid_html_contains_script(self):
        html = _mermaid_html_wrapper("flowchart TD\n A-->B", "Test")
        assert "mermaid" in html.lower()
        assert "flowchart" in html
        assert "Test" in html
        assert "<!DOCTYPE html>" in html

    def test_simple_text_svg_valid(self):
        svg = _simple_text_svg("Привет мир", "Заголовок")
        assert "<svg" in svg
        assert "Заголовок" in svg
        assert "Привет мир" in svg

    def test_build_flowchart_svg_basic(self):
        nodes = [
            {"id": "A", "label": "Старт", "shape": "ellipse"},
            {"id": "B", "label": "Работа", "shape": "rect"},
        ]
        edges = [{"from": "A", "to": "B", "label": "вперёд"}]
        svg = _build_flowchart_svg(nodes, edges, "Флоу")
        assert "<svg" in svg
        assert "Старт" in svg
        assert "Работа" in svg
        assert "Флоу" in svg
        assert "вперёд" in svg

    def test_build_flowchart_svg_no_edges(self):
        nodes = [{"id": "X", "label": "Одиноко", "shape": "rect"}]
        svg = _build_flowchart_svg(nodes, [], "Solo")
        assert "<svg" in svg
        assert "Одиноко" in svg

    def test_plantuml_online_url_returns_string(self):
        url = _plantuml_online_url("@startuml\nA -> B\n@enduml")
        assert isinstance(url, str)
        # Может быть пустой строкой при ошибке, но не None

    def test_plantuml_online_url_valid_format(self):
        url = _plantuml_online_url("@startuml\nA -> B\n@enduml")
        if url:  # если сработало кодирование
            assert url.startswith("https://www.plantuml.com/plantuml/png/")


# ══════════════════════════════════════════════════════════════════════
#  Интеграционные тесты
# ══════════════════════════════════════════════════════════════════════

class TestDiagramGeneratorIntegration:
    def setup_method(self):
        self.tool = DiagramGeneratorTool()

    def test_all_types_return_success(self, tmp_path):
        """Все 4 типа должны возвращать success=True при правильных данных."""
        cases = [
            ("mermaid",  "flowchart TD\n    A --> B",           "m.mmd"),
            ("graphviz", "digraph G { A -> B; }",               "g.dot"),
            ("svg",      json.dumps({"nodes": [{"id": "A", "label": "X", "shape": "rect"}], "edges": []}), "s.svg"),
            ("plantuml", "A -> B : hello",                       "p.puml"),
        ]
        for dtype, content, fname in cases:
            r = self.tool.execute(
                diagram_type=dtype,
                content=content,
                output_file=str(tmp_path / fname),
                render=False,
            )
            assert r.success is True, f"Тип {dtype!r} провалился: {r.error}"

    def test_result_has_duration(self, tmp_path):
        r = self.tool.execute(
            diagram_type="mermaid",
            content="flowchart TD\n A-->B",
            output_file=str(tmp_path / "d.mmd"),
        )
        assert r.success is True
        assert "duration_ms" in r.metadata
        assert r.metadata["duration_ms"] >= 0

    def test_never_raises_exception(self, tmp_path):
        """Инструмент никогда не бросает исключения."""
        r = self.tool.execute(
            diagram_type="svg",
            content="{bad json",  # некорректный JSON — должен деградировать gracefully
            output_file=str(tmp_path / "bad.svg"),
        )
        # success может быть True (деградация) или False, но не исключение
        assert r is not None
        assert isinstance(r.success, bool)
