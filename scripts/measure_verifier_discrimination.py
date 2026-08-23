"""Дискриминация детерминированного верификатора на парных утверждениях.

Пара строится так, что валидный и невалидный ответ отличаются РОВНО одним
элементом; валидный — дословный пересказ источника с правильной цитатой.
LLM-проход выключен, поэтому замер бесплатный и воспроизводимый.
"""
import collections
import sys

sys.path.insert(0, ".")

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify

N = 40

TEMPLATES = [
    ("https://example.org/sales/{i}", "Выручка за квартал составила {v} миллионов рублей."),
    ("https://example.org/stock/{i}", "На складе B2 находится {v} единиц товара."),
    ("https://example.org/bench/{i}", "Средняя задержка составила {v} миллисекунд."),
    ("https://example.org/team/{i}", "В проекте участвуют {v} инженеров."),
]
OFF_TOPIC = "Курс акций компании вырос на {v} процентов."


def _chain(url, excerpt):
    ch = ProvenanceChain()
    ch.add(make_evidence(kind="web_page", source_id=url, obtained_via="web_fetch",
                         claim="fetched page", excerpt=excerpt))
    return ch


def _accepted(report) -> bool:
    chunks = [c for c in report.chunks if c.verdict != "structural"]
    return bool(chunks) and all(c.verdict == "verified" for c in chunks)


TAB = collections.defaultdict(lambda: {"valid": [0, 0], "invalid": [0, 0]})


def run(cls, kind, answer, chain):
    rep = verify(answer=answer, chain=chain, llm=None, expects_contract_headers=False)
    TAB[cls][kind][0] += int(_accepted(rep))
    TAB[cls][kind][1] += 1


for i in range(N):
    url_t, sent_t = TEMPLATES[i % len(TEMPLATES)]
    url = url_t.format(i=i)
    v = 10 + i
    wrong = v * 10                      # заведомо другое число
    excerpt = sent_t.format(v=v)
    ch = _chain(url, excerpt)

    true_claim = f"{excerpt} [web:{url}]"

    # A. Следует ли утверждение из источника (число подменено).
    run("A. число соответствует источнику", "valid", true_claim, ch)
    run("A. число соответствует источнику", "invalid",
        f"{sent_t.format(v=wrong)} [web:{url}]", ch)

    # B. Существует ли цитируемый источник.
    run("B. цитируемый источник существует", "valid", true_claim, ch)
    run("B. цитируемый источник существует", "invalid",
        f"{excerpt} [web:https://example.org/ghost/{i}]", ch)

    # C. Относится ли утверждение к тому, что в цепочке вообще есть.
    run("C. утверждение по теме цепочки", "valid", true_claim, ch)
    run("C. утверждение по теме цепочки", "invalid",
        f"{OFF_TOPIC.format(v=v)} [web:{url}]", ch)

    # D. Есть ли цитата вообще.
    run("D. цитата присутствует", "valid", true_claim, ch)
    run("D. цитата присутствует", "invalid", excerpt, ch)

    # E. Отрицание: источник утверждает, ответ отрицает.
    run("E. полярность совпадает с источником", "valid", true_claim, ch)
    run("E. полярность совпадает с источником", "invalid",
        f"{excerpt.rstrip('.')} — неверно, это не так. [web:{url}]", ch)

print("=== ДИСКРИМИНАЦИЯ ВЕРИФИКАТОРА (структурный проход, без LLM) ===")
print(f"    {N} пар на класс, валидный = дословный пересказ источника\n")
print(f"{'класс':<40}{'принят валидный':>16}{'принят НЕвалидный':>19}{'J':>8}")
for cls in sorted(TAB):
    v_ok, v_n = TAB[cls]["valid"]
    i_ok, i_n = TAB[cls]["invalid"]
    tpr, fpr = v_ok / v_n, i_ok / i_n
    print(f"{cls:<40}{tpr:>15.0%}{fpr:>18.0%}{tpr - fpr:>+8.2f}")
