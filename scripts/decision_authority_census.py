"""Перепись решающей власти — семейство A, уровень «след детектора».

Прибор описан в `docs/audit/DECISION_AUTHORITY_CENSUS_METHOD.md` и был
СПРОЕКТИРОВАН 2026-08-21, но ни разу не запущен: запись MIR-115 прямо говорила
«not yet run». Этот скрипт — его запуск, и он умышленно даёт только ПЕРВЫЙ из
четырёх уровней счёта.

    след детектора      место найдено механически      <- считает этот скрипт
    кандидат            гипотеза об отдельном эффекте  <- НЕ считает
    измеренная          различающий тест доказал       <- НЕ считает
    классифицированная  какой из четырёх классов       <- НЕ считает

Экстраполяция между уровнями запрещена методом, и скрипт не выводит ни одного
числа выше первого уровня.

Семейство A высокорекально НАРОЧНО: оно срабатывает и на конституционных
потолках, и на способностных воротах. Специфичность принадлежит классификатору,
а не детектору; сужать A ради красивого числа значит менять настоящую полноту
на воображаемую задачу.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

_ROOTS = ("core", "app", "cli", "api", "tools")
_EXTRA_FILES = ("agent_tick.py", "main.py")

#: Имена, чей литерал ранжирует или упорядочивает работу.
_RANKING_NAME = re.compile(
    r"(^_?P_|PRIORITY|PRIO|_ORDER$|_ORDER_|ORDERED|_RANK|WEIGHT|_W_|SEVERITY)",
    re.IGNORECASE,
)

#: Слова, по которым место относится к НАЗЫВАНИЮ, ПРОИЗВОДСТВУ или
#: УПОРЯДОЧИВАНИЮ действия — фильтр достижимости из метода.
_ACTION_WORDS = re.compile(
    r"(action|task|candidate|step|plan|queue|goal|tool|command|dispatch|route|"
    r"select|choose|rank|proposal|repair)",
    re.IGNORECASE,
)

#: Имена СЛОВАРЕЙ — наборов слов для сопоставления, а не упорядоченной работы.
#: Введены после ручной выборки первого прогона 2026-08-24: из 16 проверенных
#: следов 13 оказались словарями (`_LEARNING_TERMS`, `_APPROVAL_STRONG`,
#: `_PLACEHOLDER_TLDS`), то есть 81 % ложных. Метод требует выбирать ложные
#: срабатывания РУКАМИ до публикации числа — это и есть результат той выборки.
#: Сужение бьёт по A2 и A4, а не по A1: A1 нашёл таблицу приоритетов и в
#: выборке ложных не дал.
_VOCABULARY_NAME = re.compile(
    r"(_TERMS$|_TERMS_|TERMS$|_MARKERS|_WORDS|_PATTERNS|_TLDS|_SUBSTR|"
    r"_QUALIFIERS|_PREFIXES|_SUFFIXES|_KEYWORDS|_PHRASES|_SYNONYMS|_STOP|"
    r"_EXTS|^__all__$)",
    re.IGNORECASE,
)

#: РЕСУРСНЫЕ потолки: меняют, что ВОЗМОЖНО, а не что ВЫБРАНО — класс
#: «способность» по таблице метода. Вторая ручная выборка поймала здесь
#: `_ORDER_TOKEN_CAP` — предел стоимости сравнения, пойманный по совпадению
#: имени с `_ORDER_`.
_RESOURCE_NAME = re.compile(r"(_CAP$|_CAP_|_BYTES|_CHARS|_TIMEOUT|_SECONDS)", re.IGNORECASE)


class _Hit:
    __slots__ = ("kind", "line", "name", "path", "snippet")

    def __init__(self, path: str, line: int, kind: str, name: str, snippet: str):
        self.path, self.line, self.kind = path, line, kind
        self.name, self.snippet = name, snippet

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "kind": self.kind,
                "name": self.name, "snippet": self.snippet}


def _files(repo: pathlib.Path) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in _ROOTS:
        out.extend(sorted((repo / root).rglob("*.py")))
    out.extend(repo / name for name in _EXTRA_FILES)
    return [p for p in out if p.exists()]


def _action_context(source_line: str, module_head: str) -> bool:
    """Фильтр достижимости: место должно течь в называние/производство/порядок."""
    return bool(_ACTION_WORDS.search(source_line) or _ACTION_WORDS.search(module_head))


def scan_file(path: pathlib.Path, repo: pathlib.Path) -> list[_Hit]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    lines = text.splitlines()
    head = text[:4000]
    rel = str(path.relative_to(repo)).replace("\\", "/")
    hits: list[_Hit] = []

    def line_of(node: ast.AST) -> str:
        i = getattr(node, "lineno", 0)
        return lines[i - 1].strip() if 0 < i <= len(lines) else ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if not isinstance(t, ast.Name) or not _RANKING_NAME.search(t.id):
                    continue
                if _RESOURCE_NAME.search(t.id):
                    continue  # потолок ресурса: меняет ВОЗМОЖНОЕ, не ВЫБРАННОЕ
                v = node.value
                if (
                    isinstance(v, ast.Constant)
                    and isinstance(v.value, (int, float))
                    and not isinstance(v.value, bool)
                ):
                    src = line_of(node)
                    if _action_context(src, head):
                        hits.append(_Hit(rel, node.lineno, "A1:literal-priority",
                                         t.id, src[:110]))

        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            elts = node.value.elts
            if len(elts) >= 2 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts
            ):
                t0 = node.targets[0]
                name = t0.id if isinstance(t0, ast.Name) else ""
                if name and _VOCABULARY_NAME.search(name):
                    continue  # словарь сопоставления, а не список работ
                src = line_of(node)
                if name and _action_context(src + " " + name, head):
                    hits.append(_Hit(rel, node.lineno, "A2:ordered-literal-list",
                                     name, src[:110]))

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"sorted", "max", "min"}
        ):
            literal_key = any(
                kw.arg == "key"
                and isinstance(kw.value, ast.Lambda)
                and any(isinstance(n, ast.Constant) for n in ast.walk(kw.value))
                for kw in node.keywords
            )
            src = line_of(node)
            if literal_key and _action_context(src, head):
                hits.append(_Hit(rel, node.lineno, "A3:literal-sort-key",
                                 node.func.id, src[:110]))

        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            c = node.comparators[0]
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, (int, float))
                and not isinstance(c.value, bool)
            ):
                src = line_of(node)
                # A4 сужен ручной выборкой: сравнение должно РАНЖИРОВАТЬ, а не
                # обрезать вывод (`len(goals) > 5`) и не ветвить поток
                # (`len(steps) == 1`). Требуется имя ранга рядом со сравнением.
                if _ACTION_WORDS.search(src) and _RANKING_NAME.search(src):
                    hits.append(_Hit(rel, node.lineno, "A4:literal-comparison",
                                     "", src[:110]))
    return hits


#: Контроли метода. Положительные детектор ОБЯЗАН найти; известный
#: положительный он обязан ПРОПУСТИТЬ — иначе слепой класс заявлен, а не измерен.
_POSITIVE_CONTROLS = {
    "core/autonomous_runtime.py": "_build_queue — порядок работ из литералов",
    "core/best_next_action.py": "таблица приоритетов кандидатов",
}
_MUST_MISS = "закрытый репертуар кандидатов — решение как ОТСУТСТВИЕ альтернативы"


def collect(repo: pathlib.Path) -> list[_Hit]:
    hits: list[_Hit] = []
    for path in _files(repo):
        hits.extend(scan_file(path, repo))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="census, family A, detector level")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()

    repo = pathlib.Path(__file__).resolve().parent.parent
    hits = collect(repo)

    if args.json:
        print(json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=1))
        return 0

    by_kind: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for h in hits:
        by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
        by_file[h.path] = by_file.get(h.path, 0) + 1

    print("ПЕРЕПИСЬ РЕШАЮЩЕЙ ВЛАСТИ — семейство A, уровень: след детектора")
    print("=" * 64)
    print(f"следов: {len(hits)} в {len(by_file)} файлах")
    for kind in sorted(by_kind):
        print(f"  {kind:28} {by_kind[kind]:>4}")
    print()
    print("КОНТРОЛИ")
    found = set(by_file)
    for path, what in _POSITIVE_CONTROLS.items():
        mark = "НАЙДЕН  " if path in found else "ПРОПУЩЕН"
        print(f"  {mark} положительный: {path} — {what}")
    print(f"  ПРОПУЩЕН ожидаемо: {_MUST_MISS}")
    print("    семейство A видит литералы; решение-как-отсутствие ему невидимо")
    print("    по построению — это ИЗМЕРЕННЫЙ слепой класс, а не заявленный")
    print()
    print("ЧЕГО ЭТО ЧИСЛО НЕ ЗНАЧИТ")
    print("  не «столько решений»: не кандидаты, не измеренные, не")
    print("  классифицированные. Экстраполяция между уровнями запрещена методом.")

    if args.sample and hits:
        print()
        print(f"ВЫБОРКА ДЛЯ РУЧНОЙ ПРОВЕРКИ (каждый {max(1, len(hits) // args.sample)}-й):")
        step = max(1, len(hits) // args.sample)
        for h in hits[::step][:args.sample]:
            print(f"  {h.path}:{h.line} [{h.kind}] {h.snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
