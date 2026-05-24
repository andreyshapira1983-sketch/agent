"""
tools/builtins/diagram_generator.py — Генератор диаграмм и схем

Поддерживаемые форматы:
    mermaid   — генерирует Mermaid-код (flowchart, sequence, ER, gantt, class)
                Рендерится в GitHub, GitLab, Notion, Obsidian — без бинарников
    graphviz  — генерирует DOT-источник и рендерит если установлен Graphviz binary
    svg       — генерирует SVG-файл напрямую через svgwrite
    plantuml  — генерирует PlantUML-текст (.puml файл)

params:
    diagram_type (str): mermaid | graphviz | svg | plantuml
    content      (str): Описание или код диаграммы (зависит от типа)
    output_file  (str, optional): Путь для сохранения результата
    title        (str, optional): Заголовок диаграммы (для SVG)

Для Mermaid — content содержит Mermaid-синтаксис.
Для Graphviz — content содержит DOT-синтаксис.
Для SVG — content содержит JSON-спецификацию узлов.
Для PlantUML — content содержит PlantUML-синтаксис.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_MAX_CONTENT_LEN = 50_000


class DiagramGeneratorTool(ToolBase):
    """
    Генерирует диаграммы и схемы в разных форматах.

    Mermaid: flowchart, sequence diagram, ER diagram, gantt, class diagram
    Graphviz: графы зависимостей, деревья, сети
    SVG: простые блок-схемы (из JSON-спецификации)
    PlantUML: UML диаграммы

    params:
        diagram_type (str): mermaid | graphviz | svg | plantuml
        content      (str): Код или описание диаграммы
        output_file  (str, optional): Путь для сохранения (авто если не указан)
        title        (str, optional): Заголовок (используется в SVG)
        render       (bool, optional): Рендерить в PNG/SVG через Graphviz binary (для graphviz)
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="diagram_generator",
            description=(
                "Генерирует диаграммы: Mermaid (flowchart/sequence/ER), "
                "Graphviz (DOT-граф), SVG (блок-схема), PlantUML (UML)"
            ),
            parameters={
                "diagram_type": "str — mermaid | graphviz | svg | plantuml",
                "content":      "str — код или описание диаграммы",
                "output_file":  "str (optional) — путь для сохранения файла",
                "title":        "str (optional) — заголовок диаграммы",
                "render":       "bool (optional, default True) — рендерить PNG (только для graphviz)",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        diagram_type = str(params.get("diagram_type", "")).lower().strip()
        content = str(params.get("content", "")).strip()

        if not diagram_type:
            return self._fail(
                "Параметр 'diagram_type' обязателен. "
                "Допустимо: mermaid | graphviz | svg | plantuml"
            )
        if not content:
            return self._fail("Параметр 'content' обязателен")
        if len(content) > _MAX_CONTENT_LEN:
            return self._fail(
                f"Содержимое слишком длинное: {len(content)} символов "
                f"(максимум {_MAX_CONTENT_LEN})"
            )

        t0 = time.perf_counter()

        if diagram_type == "mermaid":
            return self._generate_mermaid(content, params, t0)
        elif diagram_type == "graphviz":
            return self._generate_graphviz(content, params, t0)
        elif diagram_type == "svg":
            return self._generate_svg(content, params, t0)
        elif diagram_type == "plantuml":
            return self._generate_plantuml(content, params, t0)
        else:
            return self._fail(
                f"Неизвестный тип диаграммы: {diagram_type!r}. "
                "Допустимо: mermaid | graphviz | svg | plantuml"
            )

    # ──────────────────────────────────────────────────────────────────
    # Mermaid
    # ──────────────────────────────────────────────────────────────────

    def _generate_mermaid(self, content: str, params: dict, t0: float) -> ToolResult:
        """
        Сохраняет Mermaid-код в .mmd файл.
        Mermaid рендерится в браузере, GitHub, Notion, Obsidian без бинарников.
        """
        output_file = params.get("output_file", "diagram.mmd")
        path = Path(str(output_file))
        if path.suffix.lower() not in {".mmd", ".md", ".txt"}:
            path = path.with_suffix(".mmd")

        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return self._fail(f"Не удалось сохранить файл: {exc}")

        # Также создаём HTML-обёртку для открытия в браузере
        html_path = path.with_suffix(".html")
        html_content = _mermaid_html_wrapper(content, params.get("title", "Диаграмма"))
        try:
            html_path.write_text(html_content, encoding="utf-8")
        except Exception:
            html_path = None

        elapsed = (time.perf_counter() - t0) * 1000
        msg = (
            f"Mermaid-диаграмма сохранена:\n"
            f"  • Исходник (.mmd): {path}\n"
        )
        if html_path:
            msg += f"  • HTML-превью: {html_path} (открыть в браузере)\n"
        msg += (
            "\nГде отображается автоматически:\n"
            "  • GitHub / GitLab (в Markdown-блоках ```mermaid)\n"
            "  • Notion, Obsidian, VS Code (с расширением)\n"
            "  • https://mermaid.live — онлайн-редактор"
        )

        return self._ok(
            output=msg,
            duration_ms=round(elapsed, 2),
            file=str(path),
            html_preview=str(html_path) if html_path else None,
        )

    # ──────────────────────────────────────────────────────────────────
    # Graphviz (DOT)
    # ──────────────────────────────────────────────────────────────────

    def _generate_graphviz(self, content: str, params: dict, t0: float) -> ToolResult:
        """
        Сохраняет DOT-источник и опционально рендерит PNG/SVG через Graphviz binary.
        """
        output_file = params.get("output_file", "diagram.dot")
        path = Path(str(output_file))
        if path.suffix.lower() not in {".dot", ".gv"}:
            path = path.with_suffix(".dot")

        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return self._fail(f"Не удалось сохранить DOT-файл: {exc}")

        render_flag = params.get("render", True)
        rendered_path: str | None = None
        render_note = ""

        if render_flag:
            try:
                import graphviz

                # Определяем движок (dot, neato, fdp, sfdp, circo, twopi)
                engine = "dot"
                if "neato" in content[:500].lower():
                    engine = "neato"

                src = graphviz.Source(content, engine=engine)
                # Рендерим в PNG рядом с DOT-файлом
                out_stem = str(path.with_suffix(""))
                rendered = src.render(filename=out_stem, format="png", cleanup=False)
                rendered_path = rendered
                render_note = f"\n  • PNG: {rendered}"

            except ImportError:
                render_note = (
                    "\n  ⚠ graphviz Python-пакет не установлен (pip install graphviz). "
                    "DOT-файл сохранён — откройте на https://dreampuf.github.io/GraphvizOnline"
                )
            except Exception as exc:
                render_note = (
                    f"\n  ⚠ Рендеринг не удался: {exc}. "
                    "Проверьте, установлен ли Graphviz binary: "
                    "https://graphviz.org/download/"
                )

        elapsed = (time.perf_counter() - t0) * 1000
        msg = (
            f"Graphviz-диаграмма сохранена:\n"
            f"  • DOT-источник: {path}"
            f"{render_note}\n\n"
            "Онлайн-рендеринг без установки:\n"
            "  • https://dreampuf.github.io/GraphvizOnline\n"
            "  • https://viz-js.com"
        )
        return self._ok(
            output=msg,
            duration_ms=round(elapsed, 2),
            dot_file=str(path),
            png_file=rendered_path,
        )

    # ──────────────────────────────────────────────────────────────────
    # SVG
    # ──────────────────────────────────────────────────────────────────

    def _generate_svg(self, content: str, params: dict, t0: float) -> ToolResult:
        """
        Генерирует SVG из JSON-спецификации или напрямую.

        JSON-спецификация (упрощённая блок-схема):
        {
            "nodes": [
                {"id": "A", "label": "Начало", "shape": "ellipse"},
                {"id": "B", "label": "Обработка", "shape": "rect"},
                {"id": "C", "label": "Конец", "shape": "ellipse"}
            ],
            "edges": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C", "label": "готово"}
            ]
        }

        Если content начинается с "<svg", сохраняется напрямую как SVG.
        """
        output_file = params.get("output_file", "diagram.svg")
        path = Path(str(output_file))
        if path.suffix.lower() != ".svg":
            path = path.with_suffix(".svg")

        title = params.get("title", "Диаграмма")

        # Если передан готовый SVG — сохраняем как есть
        if content.strip().lower().startswith("<svg") or content.strip().startswith("<?xml"):
            try:
                path.write_text(content, encoding="utf-8")
            except Exception as exc:
                return self._fail(f"Не удалось сохранить SVG: {exc}")
            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                output=f"SVG сохранён: {path}",
                duration_ms=round(elapsed, 2),
                file=str(path),
            )

        # Пытаемся разобрать JSON-спецификацию
        try:
            spec = json.loads(content)
        except json.JSONDecodeError:
            # Попытка сохранить как есть (пользователь мог передать не-JSON не-SVG)
            svg_text = _simple_text_svg(content, title)
            try:
                path.write_text(svg_text, encoding="utf-8")
            except Exception as exc:
                return self._fail(f"Не удалось сохранить SVG: {exc}")
            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                output=f"SVG (текстовый) сохранён: {path}",
                duration_ms=round(elapsed, 2),
                file=str(path),
            )

        # Генерируем блок-схему из JSON
        nodes = spec.get("nodes", [])
        edges = spec.get("edges", [])

        try:
            svg_text = _build_flowchart_svg(nodes, edges, title)
        except Exception as exc:
            return self._fail(f"Ошибка генерации SVG: {exc}")

        try:
            path.write_text(svg_text, encoding="utf-8")
        except Exception as exc:
            return self._fail(f"Не удалось сохранить SVG: {exc}")

        elapsed = (time.perf_counter() - t0) * 1000
        return self._ok(
            output=f"SVG блок-схема ({len(nodes)} узлов, {len(edges)} связей) сохранена: {path}",
            duration_ms=round(elapsed, 2),
            file=str(path),
            nodes=len(nodes),
            edges=len(edges),
        )

    # ──────────────────────────────────────────────────────────────────
    # PlantUML
    # ──────────────────────────────────────────────────────────────────

    def _generate_plantuml(self, content: str, params: dict, t0: float) -> ToolResult:
        """
        Сохраняет PlantUML-код в .puml файл.
        Для рендеринга используется публичный API plantuml.com.
        """
        output_file = params.get("output_file", "diagram.puml")
        path = Path(str(output_file))
        if path.suffix.lower() not in {".puml", ".pu", ".plantuml"}:
            path = path.with_suffix(".puml")

        # Добавляем @startuml/@enduml если не указаны
        text = content.strip()
        if not text.startswith("@start"):
            text = f"@startuml\n{text}\n@enduml"

        try:
            path.write_text(text, encoding="utf-8")
        except Exception as exc:
            return self._fail(f"Не удалось сохранить PUML-файл: {exc}")

        # Генерируем ссылку на plantuml.com для онлайн-рендеринга
        online_url = _plantuml_online_url(text)

        elapsed = (time.perf_counter() - t0) * 1000
        msg = (
            f"PlantUML-диаграмма сохранена:\n"
            f"  • Исходник (.puml): {path}\n"
        )
        if online_url:
            msg += f"  • Онлайн-превью: {online_url}\n"
        msg += (
            "\nДля локального рендеринга:\n"
            "  • VS Code: расширение 'PlantUML'\n"
            "  • CLI: java -jar plantuml.jar diagram.puml\n"
            "  • https://www.plantuml.com/plantuml/uml/"
        )

        return self._ok(
            output=msg,
            duration_ms=round(elapsed, 2),
            file=str(path),
            online_url=online_url,
        )


# ══════════════════════════════════════════════════════════════════════
#  Вспомогательные функции
# ══════════════════════════════════════════════════════════════════════

def _mermaid_html_wrapper(mermaid_code: str, title: str) -> str:
    """Генерирует HTML-файл с встроенным Mermaid (через CDN)."""
    escaped = mermaid_code.replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 20px; background: #f9f9f9; }}
    h1 {{ color: #333; }}
    .mermaid {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.1); }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="mermaid">
{mermaid_code}
  </div>
  <script>mermaid.initialize({{ startOnLoad: true, theme: 'default' }});</script>
</body>
</html>"""


def _simple_text_svg(text: str, title: str) -> str:
    """Создаёт простой SVG с текстом — фолбэк когда не JSON."""
    lines = text.split("\n")[:30]
    height = max(100, 40 + len(lines) * 20)
    rows = "\n".join(
        f'  <text x="20" y="{40 + i * 20}" font-size="14">{_svg_escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="{height}">\n'
        f'  <rect width="100%" height="100%" fill="#f9f9f9"/>\n'
        f'  <text x="20" y="25" font-size="18" font-weight="bold">{_svg_escape(title)}</text>\n'
        f'{rows}\n'
        f'</svg>'
    )


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _build_flowchart_svg(
    nodes: list[dict],
    edges: list[dict],
    title: str,
) -> str:
    """
    Генерирует SVG блок-схему из списка узлов и рёбер.
    Узлы раскладываются вертикально по оси Y.
    """
    NODE_W, NODE_H = 160, 50
    H_GAP, V_GAP = 60, 80
    MARGIN = 40

    # Простое вертикальное расположение
    node_pos: dict[str, tuple[int, int]] = {}
    for i, node in enumerate(nodes):
        x = MARGIN + (i % 3) * (NODE_W + H_GAP)
        y = MARGIN + 60 + (i // 3) * (NODE_H + V_GAP)
        node_pos[node["id"]] = (x, y)

    total_rows = (len(nodes) + 2) // 3
    svg_w = MARGIN * 2 + 3 * (NODE_W + H_GAP)
    svg_h = MARGIN * 2 + 60 + total_rows * (NODE_H + V_GAP) + 40

    parts = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}">',
        f'  <defs>',
        f'    <marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">',
        f'      <polygon points="0 0, 10 3.5, 0 7" fill="#555"/>',
        f'    </marker>',
        f'  </defs>',
        f'  <rect width="100%" height="100%" fill="#f0f4f8"/>',
        f'  <text x="{svg_w // 2}" y="35" text-anchor="middle" font-size="20" '
        f'font-weight="bold" fill="#2d3748">{_svg_escape(title)}</text>',
    ]

    # Рёбра
    for edge in edges:
        src = edge.get("from", "")
        dst = edge.get("to", "")
        if src not in node_pos or dst not in node_pos:
            continue
        x1, y1 = node_pos[src]
        x2, y2 = node_pos[dst]
        cx1 = x1 + NODE_W // 2
        cy1 = y1 + NODE_H
        cx2 = x2 + NODE_W // 2
        cy2 = y2
        label = edge.get("label", "")
        mid_x = (cx1 + cx2) // 2
        mid_y = (cy1 + cy2) // 2

        parts.append(
            f'  <line x1="{cx1}" y1="{cy1}" x2="{cx2}" y2="{cy2}" '
            f'stroke="#555" stroke-width="2" marker-end="url(#arrow)"/>'
        )
        if label:
            parts.append(
                f'  <text x="{mid_x}" y="{mid_y - 4}" text-anchor="middle" '
                f'font-size="12" fill="#4a5568">{_svg_escape(label)}</text>'
            )

    # Узлы
    for node in nodes:
        nid = node.get("id", "")
        label = node.get("label", nid)
        shape = node.get("shape", "rect").lower()
        color = node.get("color", "#4299e1")

        if nid not in node_pos:
            continue
        x, y = node_pos[nid]
        cx = x + NODE_W // 2
        cy = y + NODE_H // 2

        if shape in {"ellipse", "circle", "oval"}:
            parts.append(
                f'  <ellipse cx="{cx}" cy="{cy}" rx="{NODE_W // 2}" ry="{NODE_H // 2}" '
                f'fill="{color}" stroke="#2b6cb0" stroke-width="2"/>'
            )
        elif shape in {"diamond", "rhombus"}:
            pts = (
                f"{cx},{y} {x + NODE_W},{cy} {cx},{y + NODE_H} {x},{cy}"
            )
            parts.append(
                f'  <polygon points="{pts}" fill="{color}" stroke="#2b6cb0" stroke-width="2"/>'
            )
        else:  # rect
            rx = "8" if node.get("rounded") else "4"
            parts.append(
                f'  <rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" '
                f'rx="{rx}" fill="{color}" stroke="#2b6cb0" stroke-width="2"/>'
            )

        parts.append(
            f'  <text x="{cx}" y="{cy + 5}" text-anchor="middle" '
            f'font-size="13" font-family="Arial" fill="white" font-weight="bold">'
            f'{_svg_escape(label)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _plantuml_online_url(text: str) -> str:
    """
    Кодирует PlantUML-текст для plantuml.com онлайн-рендеринга.
    Использует стандартное zlib-сжатие + base64 с алфавитом PlantUML.
    """
    try:
        import zlib
        import base64

        data = text.encode("utf-8")
        compressed = zlib.compress(data, 9)[2:-4]  # убираем zlib-заголовок

        # PlantUML использует модифицированный base64
        _alphabet = (
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
        )
        _std = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

        b64 = base64.b64encode(compressed).decode("ascii")
        encoded = "".join(
            _alphabet[_std.index(c)] if c in _std else c
            for c in b64
        )
        return f"https://www.plantuml.com/plantuml/png/{encoded}"
    except Exception:
        return ""
