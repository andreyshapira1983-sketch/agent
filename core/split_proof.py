"""Доказательство того, что модуль надо переделать, — а не его толщина.

Замер 2026-09-21. `core/drives.py:self_improvement_targets` выбирал цель
самоправки по числу строк: все модули по убыванию, первый — цель. Лестница
того дня: step_sanitizer 1223, best_next_action 1150, loop_step_execution
1122, knowledge_pipeline 1074, campaign_io 1067, charter_goal 1041 — шесть
файлов в пределах 180 строк. Разрежешь первый — корона переедет на второй:
финиша нет по построению. 141 самостоятельная цель, 139 из них «прочитать
себя» или «разбить себя»; из десяти заявок на самоправку ни одна строка не
пережила двух суток.

Требование оператора: прежде чем резать, ДОКАЗАТЬ, что резать надо, и
доказательство бывает трёх видов — дубль, чужой дом, громоздкость. Размер
сам по себе доказательством не является, он только повод посмотреть.

Признаки ниже предложил сам агент (разговор 21.09 05:15), здесь они
посчитаны по коду, без модели:

* ``dup`` — тело определения дословно (по AST, без имени и докстринга)
  повторяет определение в другом модуле. Правка — СВЕСТИ повтор, а не резать.
* ``multi_subject`` — если снять входы, которыми модуль пользуются снаружи,
  его определения распадаются на ≥2 несвязанные группы, каждая не меньше
  порога. Это два предмета в одном файле; правка — вынести группу в модуль,
  названный по её смыслу. Дома остаётся группа, чьи имена совпадают с именем
  модуля (`core/verifier_absence.py` — это ворота отсутствия, выносить надо
  другие ворота, а не их).

Нет ни одного — ``none``: файл не цель, даже если он самый большой в дереве.

ЧУЖОГО ДОМА здесь НЕТ, и это решение, а не пропуск. Третий вид оператора —
«функция принадлежит другому существующему файлу» — первым делом был посчитан
правилом «функцию не зовут в своём модуле, а из чужих берёт ровно один».
Прогон по настоящему коду 2026-09-21: 110 срабатываний из 311 модулей, и
почти все ложные — правило объявило, что каждая примесь цикла
(`AgentLoopSynthesis`, `AgentLoopStepExecution`, …) живёт «не у себя» и её
надо вернуть в `core/loop.py`. То есть погнало бы агента отменять намеренный
раскол, сделанный оператором. Модуль с одним клиентом — это обычный модуль.
Надёжного признака дома (по предмету, по общим данным, по родне) пока нет;
пока его нет, этот вид не выдаёт цели вовсе — меньше целей, но все настоящие.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: Где живёт собственный код агента.
CODE_DIRS = ("core", "tools", "app", "cli")
#: Пороги — сколько строк должно стоять за доказательством, чтобы правка
#: стоила заявки. Ниже — косметика того же рода, что и раньше.
MIN_DUP_LOC = 12
MIN_SUBJECT_LOC = 120
_KIND_RANK = {"dup": 0, "multi_subject": 1}
#: Слова имени модуля, которые ничего не говорят о его предмете.
_GENERIC_STEM = frozenset({"core", "tools", "helpers", "utils", "types", "io", "base"})


@dataclass(frozen=True)
class Definition:
    name: str
    loc: int
    refs: frozenset[str]
    shape: str


@dataclass
class Module:
    rel: str
    lines: int
    defs: dict[str, Definition] = field(default_factory=dict)
    #: имена, использованные на уровне модуля вне определений
    top_refs: set[str] = field(default_factory=set)
    #: (модуль-источник, имя) — что этот модуль берёт у других
    imports: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class Proof:
    kind: str                       # dup | multi_subject
    loc: int                        # сколько строк стоит за доказательством
    names: tuple[str, ...]          # какие определения
    other: str = ""                 # для dup — модуль с копией
    groups: tuple[tuple[str, ...], ...] = ()   # для multi_subject

    def describe(self, rel: str) -> str:
        names = ", ".join(self.names[:6]) + (" и др." if len(self.names) > 6 else "")
        if self.kind == "dup":
            return (f"дубль: в {rel} определения {names} дословно повторяют "
                    f"{self.other} ({self.loc} строк)")
        sizes = ", ".join(str(len(g)) for g in self.groups)
        return (f"два предмета: без внешних входов определения {rel} распадаются "
                f"на {len(self.groups)} несвязанные группы (по {sizes} имён); "
                f"выносимая группа: {names} ({self.loc} строк)")


def _shape(node: ast.AST) -> str:
    body = list(getattr(node, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return "|".join(ast.dump(stmt, annotate_fields=False) for stmt in body)


def _names_in(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _module_rel(dotted: str) -> str:
    return dotted.replace(".", "/") + ".py"


def _read(path: Path, rel: str) -> Module | None:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return None
    mod = Module(rel=rel, lines=len(src.splitlines()))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            mod.defs[node.name] = Definition(
                name=node.name, loc=end - node.lineno + 1,
                refs=frozenset(_names_in(node) - {node.name}), shape=_shape(node))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            src_rel = _module_rel(node.module)
            mod.imports.update((src_rel, a.name) for a in node.names)
        elif isinstance(node, ast.Import):
            # `import core.x` — такая же зависимость на загрузке, как from-импорт;
            # без неё направление дубля (ниже) видело бы не все рёбра.
            mod.imports.update((_module_rel(a.name), "") for a in node.names)
        else:
            mod.top_refs |= _names_in(node)
    return mod


def index_workspace(root: Path | str) -> dict[str, Module]:
    """Все модули собственного кода агента, разобранные один раз."""
    base = Path(root)
    out: dict[str, Module] = {}
    for folder in CODE_DIRS:
        for path in sorted((base / folder).glob("*.py")):
            rel = f"{folder}/{path.name}"
            mod = _read(path, rel)
            if mod is not None:
                out[rel] = mod
    return out


def _users(index: dict[str, Module]) -> dict[tuple[str, str], set[str]]:
    users: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mod in index.values():
        for src_rel, name in mod.imports:
            if src_rel != mod.rel and name:
                users[(src_rel, name)].add(mod.rel)
    return users


def _dependents(index: dict[str, Module], rel: str) -> set[str]:
    """Модули, которые загружают `rel` — сами или через цепочку импортов."""
    importers: dict[str, set[str]] = defaultdict(set)
    for mod in index.values():
        for src_rel, _name in mod.imports:
            importers[src_rel].add(mod.rel)
    seen: set[str] = set()
    stack = [rel]
    while stack:
        for user in importers.get(stack.pop(), ()):
            if user not in seen:
                seen.add(user)
                stack.append(user)
    return seen


def _dup_proof(mod: Module, index: dict[str, Module]) -> Proof | None:
    """Дубль в `mod`, который можно убрать, взяв копию импортом из другого модуля.

    Дом копии обязан не зависеть от `mod`. Эпизод 2026-09-21: цель велела
    оставить `_bool`/`_parse_iso` в core/scheduler.py и импортировать их в
    core/task_queue.py — а scheduler.py:21 сам импортирует task_queue, и правка
    замкнула бы импорт в круг. Такой дом не годится; правка идёт с другой
    стороны — у того, кто уже зависит.
    """
    by_shape: dict[str, list[tuple[str, str]]] = defaultdict(list)
    depends_on_mod = _dependents(index, mod.rel)
    for other in index.values():
        if other.rel == mod.rel or other.rel in depends_on_mod:
            continue
        for d in other.defs.values():
            if d.loc >= 5 and d.shape:
                by_shape[d.shape].append((other.rel, d.name))
    per_other: dict[str, list[Definition]] = defaultdict(list)
    for d in mod.defs.values():
        if d.loc >= 5 and d.shape in by_shape:
            per_other[by_shape[d.shape][0][0]].append(d)
    if not per_other:
        return None
    other, defs = max(per_other.items(), key=lambda kv: sum(d.loc for d in kv[1]))
    loc = sum(d.loc for d in defs)
    if loc < MIN_DUP_LOC:
        return None
    return Proof("dup", loc, tuple(sorted(d.name for d in defs)), other=other)


def _stem_tokens(rel: str) -> set[str]:
    stem = Path(rel).stem.casefold()
    return {t for t in stem.split("_") if len(t) >= 4 and t not in _GENERIC_STEM}


def _owns_the_module(group: list[str], stem: set[str]) -> bool:
    return any(tok in name.casefold() for name in group for tok in stem)


def _subject_proof(mod: Module, users: dict[tuple[str, str], set[str]]) -> Proof | None:
    inner = {n: d for n, d in mod.defs.items() if not users.get((mod.rel, n))}
    parent = {n: n for n in inner}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for n, d in inner.items():
        for ref in d.refs & inner.keys():
            parent[find(n)] = find(ref)
    groups: dict[str, list[str]] = defaultdict(list)
    for n in inner:
        groups[find(n)].append(n)
    big = sorted((sorted(g) for g in groups.values()
                  if sum(inner[n].loc for n in g) >= MIN_SUBJECT_LOC),
                 key=lambda g: -sum(inner[n].loc for n in g))
    if len(big) < 2:
        return None
    # Дома остаётся предмет, названный так же, как модуль; выносится самая
    # крупная из ЧУЖИХ групп. Если имя модуля не говорит ни об одной —
    # выносится меньшая, как наименее своя.
    stem = _stem_tokens(mod.rel)
    foreign = [g for g in big if not _owns_the_module(g, stem)]
    moved = foreign[0] if foreign and len(foreign) < len(big) else big[-1]
    return Proof("multi_subject", sum(inner[n].loc for n in moved), tuple(moved),
                 groups=tuple(tuple(g) for g in big))


def proof_for(rel: str, index: dict[str, Module]) -> Proof | None:
    """Самое сильное доказательство для модуля, или None — модуль не цель."""
    mod = index.get(rel)
    if mod is None:
        return None
    return _dup_proof(mod, index) or _subject_proof(mod, _users(index))


def rank_key(proof: Proof, lines: int) -> tuple[int, int, int]:
    """Сначала вид доказательства, потом его вес; размер файла — последний."""
    return (_KIND_RANK[proof.kind], -proof.loc, -lines)
