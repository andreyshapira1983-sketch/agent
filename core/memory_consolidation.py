"""Сверка нового вывода с памятью ПЕРЕД записью — фаза обновления Mem0.

Источник: Chhikara et al., «Mem0: Building Production-Ready AI Agents with
Scalable Long-Term Memory», arXiv 2504.19413, раздел 3.1 и Алгоритм 1
(приложение B). Для каждого нового факта достаются top-s похожих записей, и
МОДЕЛЬ выбирает одну из четырёх операций:

- ADD — равнозначной записи нет, пишется новая;
- UPDATE — новый дополняет прежний; они СЛИВАЮТСЯ в один вывод под прежним
  вопросом (так в исходниках Mem0, mem0/configs/prompts.py: «Likes cheese
  pizza» + «Loves chicken pizza» -> «Loves cheese and chicken pizza»; статья
  пишет «заменить более богатым», но код сливает — и только слияние не теряет
  знания, когда модель склеит двух близких соседей);
- DELETE — новый противоречит прежнему; прежний убирается;
- NOOP — это уже есть в памяти.

Зачем, замер 2026-09-23 на живой памяти: двенадцать записей об одной и той же
теореме Нётер у Тонга, одиннадцать о лексическом анализе у Могенсена, семь
разборов одного случая. Дубль-сторож при записи (`memory_hygiene`, похожесть
текста не ниже 0.85) пропустил все: выводы об одном факте, написанные разными
словами, до 0.85 не дотягивают. Структурный ключ (`learned_conclusion`) ловит
повтор только когда в вопросе назван термин в скобках или кавычках. Порог
похожести решить этого не может в принципе: противоречие похоже на исходник
сильнее, чем пересказ, и предельная точность любого порога — 0.67 (замерено в
литературе, см. `learned_conclusion.question_key`). Поэтому, как у Mem0,
похожесть лишь ОТБИРАЕТ кандидатов (здесь — BM25 из `memory_policy`), а
решает модель.

Заодно — заголовок вместо письма (ReasoningBank, arXiv 2509.25140: в память
кладётся выжимка с кратким заголовком, а не сырой обмен). Замер того же дня:
у 15 записей «вопросом» стояло письмо или объявление о вакансии, обрезанное
на 300-м знаке, и оно служило ключом записи.

Живой прогон 2026-09-23 на настоящей модели (DeepSeek, как у агента): из 20
архивных повторов узнано 19 (все 11 о Нётер); из 20 контрольных разных
вопросов к одной книге 16 — ADD, а 4 раза UPDATE — две пары близких соседей в
одном разделе (инвариант и псевдокод Дейкстры; волны на поверхности и мелкая
вода), в обе стороны. Первая редакция ЗАМЕНЯЛА прежнюю запись более богатой —
и эти 4 случая стёрли бы знание; слияние сохранило в слитых текстах 205 из 208
чисел обеих записей (разделы, страницы, строки, формулы). Отсюда слияние. Ещё
одна потеря первой редакции: модели показывались первые 700 знаков записи, и
хвост терялся при слиянии («стр. 24» в конце записи о Нётер) — теперь запись
показывается целиком.

Всё обратимо: «убрать» значит перенести в архив. Сбой модели, пустой или
неразборчивый ответ, ссылка на несуществующую запись — это ADD, как было до
правки: вывод не теряется никогда.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Сколько похожих выводов показывать модели (s в Алгоритме 1 Mem0).
TOP_S = 5
#: Длиннее — это не вопрос, а письмо или вставленный текст: просить заголовок.
LONG_QUESTION_CHARS = 200
#: Предел заголовка.
MAX_TITLE_CHARS = 160
#: Запись показывается модели целиком: хвост, срезанный на 700 знаках, при
#: слиянии терялся (живой прогон: «стр. 24» в конце записи о Нётер).
_PREVIEW_CHARS = 1300
#: Предел слитого вывода: два вывода по 600 знаков.
MAX_MERGED_CHARS = 1200
_OPERATIONS = ("ADD", "UPDATE", "DELETE", "NOOP")

SYSTEM = """You maintain an agent's long-term memory of conclusions. A NEW conclusion is about to be stored.
Below are EXISTING conclusions that look similar. Choose exactly one operation (Mem0 update phase):
- ADD: no existing conclusion states the same fact; the new one is new knowledge.
- UPDATE: the new one is about the same thing as ONE existing conclusion and adds complementary information; you then write "merged": ONE conclusion that keeps EVERYTHING from BOTH (e.g. "Likes cheese pizza" + "Loves chicken pizza" -> "Loves cheese and chicken pizza"). Never drop a fact, number, section, page or quote from either; drop only repeated wording; at most 1100 characters.
- DELETE: the new one contradicts ONE existing conclusion about the same thing.
- NOOP: the existing conclusion already says the same thing; the new one adds nothing (different wording is still NOOP).
Two conclusions about different questions (different theorem, different section, different file, different case) are ADD even if they share words.
If the QUESTION given is long (a letter or pasted text), also write "title": one short question (max 150 characters, same language) naming what was actually asked; keep file paths and any names in parentheses or quotes exactly as written. Otherwise "title": null.
Answer with ONE JSON object only:
{"operation": "ADD|UPDATE|DELETE|NOOP", "target": "<id of the existing conclusion, or null>", "merged": "<for UPDATE only: the merged conclusion, else null>", "title": "<short question or null>"}"""


@dataclass(frozen=True)
class Consolidation:
    """Решение по новому выводу."""

    operation: str               # ADD | UPDATE | DELETE | NOOP
    target_id: str | None = None
    merged: str | None = None
    title: str | None = None
    reason: str = ""


def similar_conclusions(content: str, records: list[Any], top_s: int = TOP_S) -> list[Any]:
    """До top_s прежних ВЫВОДОВ, похожих на новый, по BM25; только с общими словами."""
    from core.memory_policy import _bm25_scores, _term_counts, _tokens

    pool = [r for r in records if "conclusion" in (getattr(r, "tags", None) or [])]
    if not pool:
        return []
    from core.memory_embeddings import fused_relevance

    texts = [_without_sources(str(r.content)) for r in pool]
    lexical = _bm25_scores(_tokens(_without_sources(content)), [_term_counts(t, []) for t in texts])
    # Mem0 достаёт top-s похожих по векторам; без смысла — по общим словам.
    fused, semantic = fused_relevance(_without_sources(content), texts, lexical)
    ranked = sorted(range(len(pool)), key=lambda i: -fused[i])
    return [pool[i] for i in ranked if semantic is not None or lexical[i] > 0][:top_s]


def _without_sources(content: str) -> str:
    keep = [ln for ln in (content or "").splitlines()
            if not ln.startswith(("Источники:", "Источник:"))]
    return "\n".join(keep)


def needs_title(question: str) -> bool:
    return len(" ".join((question or "").split())) > LONG_QUESTION_CHARS


def consolidate(llm: Any, content: str, question: str, similar: list[Any]) -> Consolidation:
    """Спросить модель; любой сбой — ADD."""
    long_q = needs_title(question)
    if llm is None or (not similar and not long_q):
        return Consolidation("ADD", reason="nothing similar stored")
    listing = "\n\n".join(
        f"[{r.id}]\n{_without_sources(str(r.content))[:_PREVIEW_CHARS]}" for r in similar
    ) or "(none)"
    user = (
        f"QUESTION ({'long — give a title' if long_q else 'short — title null'}):\n"
        f"{' '.join(question.split())[:1500]}\n\n"
        f"NEW conclusion:\n{_without_sources(content)[:_PREVIEW_CHARS]}\n\n"
        f"EXISTING similar conclusions:\n{listing}"
    )
    try:
        raw = llm.complete(SYSTEM, user, max_tokens=900, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — сбой модели не должен терять вывод
        return Consolidation("ADD", reason=f"model call failed: {type(exc).__name__}")
    from core.plan_parsing import extract_json_object

    data = extract_json_object(raw if isinstance(raw, str) else str(raw)) or {}
    op = str(data.get("operation") or "").strip().upper()
    title = data.get("title")
    title = " ".join(str(title).split())[:MAX_TITLE_CHARS] if long_q and title else None
    if op not in _OPERATIONS:
        return Consolidation("ADD", title=title, reason="unreadable model answer")
    target = data.get("target")
    ids = {r.id for r in similar}
    if op != "ADD" and target not in ids:
        return Consolidation("ADD", title=title, reason=f"{op} named no known record ({target!r})")
    merged = " ".join(str(data.get("merged") or "").split())[:MAX_MERGED_CHARS] or None
    if op == "UPDATE" and not merged:
        return Consolidation("ADD", title=title, reason="UPDATE without merged text: both kept")
    return Consolidation(
        op, target_id=target if op != "ADD" else None,
        merged=merged if op == "UPDATE" else None, title=title, reason="model",
    )


#: Ворота слияния — «Useful Memories Become Faulty» (arXiv 2605.12978): память,
#: которую модель непрерывно переписывает, сначала полезнее, потом хуже, чем
#: без памяти; корень — шаг слияния, не опыт. Рецепт: сырое хранить как улику,
#: слияние пропускать через ворота, а не запускать после каждого хода.
#: Замер 2026-09-25 на сервере: 8 слияний (UPDATE), удалений 0, все 8 — один
#: и тот же вопрос, уточнённый. Номер цели и раньше сверялся с показанными
#: кандидатами (consolidate выше); не проверялось, что слитое не потеряло знаний.
#:
#: Мерка ворот — та, которой этот модуль был принят 23.09: слияние сохранило
#: 205 из 208 чисел обеих записей (разделы, страницы, строки, формулы). Слитый
#: текст, потерявший хоть одно число старой или новой записи, не пишется —
#: обе записи остаются рядом. Первая редакция ворот сравнивала СЛОВА вопросов
#: и остановила каноническое слияние («формулировка теоремы Нётер у Тонга» +
#: «пример теоремы Нётер у Тонга»: 2 общих основы из 11) — словами предмет не
#: определить, это делает модель; ворота проверяют, что она ничего не выронила.
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
_NESTED = re.compile(r"^\s*Вопрос:.*?Вывод:\s*", re.DOTALL)


def _numbers(text: str) -> set[str]:
    """Числа ВЫВОДА: строка «Вопрос: …» по замыслу берётся у прежней записи."""
    body = "\n".join(ln for ln in _without_sources(text or "").splitlines() if not ln.startswith("Вопрос:"))
    return set(_NUMBER.findall(body))


def gate(decision: Consolidation, new_content: str, candidates: list[Any]) -> Consolidation:
    """UPDATE — только над показанным кандидатом и без потери чисел; иначе ADD."""
    if decision.operation not in ("UPDATE", "DELETE"):
        return decision
    target = next((r for r in candidates if getattr(r, "id", None) == decision.target_id), None)
    if target is None:
        return Consolidation("ADD", title=decision.title,
                             reason=f"gate: {decision.operation} target is not among the shown candidates")
    if decision.operation == "UPDATE":
        lost = sorted((_numbers(str(target.content)) | _numbers(new_content)) - _numbers(decision.merged or ""))
        if lost:
            return Consolidation("ADD", title=decision.title,
                                 reason=f"gate: the merge would drop {', '.join(lost[:5])}; both kept")
    return decision


def merged_content(new_content: str, target_content: str, merged: str) -> str:
    """Слитая запись: вопрос прежней записи, слитый вывод, источники обеих.

    Вопрос берётся у прежней записи, чтобы её ключ не поплыл. Модель порой
    повторяет в слитом выводе «Вопрос: … Вывод: …», и при каждом слиянии
    вложенность росла (живая память 2026-09-25) — такие головы срезаются.
    """
    while _NESTED.match(merged or ""):
        merged = _NESTED.sub("", merged, count=1)
    def line(content: str, prefix: str) -> str:
        return next((ln[len(prefix):].strip() for ln in (content or "").splitlines()
                     if ln.startswith(prefix)), "")

    sources: list[str] = []
    for content in (target_content, new_content):
        for s in line(content, "Источники: ").split(","):
            if s.strip() and s.strip() not in sources:
                sources.append(s.strip())
    head = (target_content or "").split("\n", 1)[0]
    out = f"{head}\nВывод: {merged}"
    return out + (f"\nИсточники: {', '.join(sources)[:300]}" if sources else "")


def with_title(content: str, title: str | None) -> str:
    """Заменить строку «Вопрос: …» заголовком; остальное как было."""
    if not title:
        return content
    head, sep, rest = (content or "").partition("\n")
    if not head.startswith("Вопрос: "):
        return content
    return f"Вопрос: {title}{sep}{rest}"
