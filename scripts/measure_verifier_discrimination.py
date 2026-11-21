"""Measure how the deterministic verifier separates claim pairs that differ in one element."""
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
    wrong = v * 10                      # surely a different number
    excerpt = sent_t.format(v=v)
    ch = _chain(url, excerpt)

    true_claim = f"{excerpt} [web:{url}]"

    # A. The number is swapped.
    run("A. число соответствует источнику", "valid", true_claim, ch)
    run("A. число соответствует источнику", "invalid",
        f"{sent_t.format(v=wrong)} [web:{url}]", ch)

    # B. The cited source does not exist.
    run("B. цитируемый источник существует", "valid", true_claim, ch)
    run("B. цитируемый источник существует", "invalid",
        f"{excerpt} [web:https://example.org/ghost/{i}]", ch)

    # C. The claim is off the chain's topic.
    run("C. утверждение по теме цепочки", "valid", true_claim, ch)
    run("C. утверждение по теме цепочки", "invalid",
        f"{OFF_TOPIC.format(v=v)} [web:{url}]", ch)

    # D. No citation at all.
    run("D. цитата присутствует", "valid", true_claim, ch)
    run("D. цитата присутствует", "invalid", excerpt, ch)

    # E. The source asserts, the answer negates.
    run("E. полярность совпадает с источником", "valid", true_claim, ch)
    run("E. полярность совпадает с источником", "invalid",
        f"{excerpt.rstrip('.')} — неверно, это не так. [web:{url}]", ch)

# ── F, G: forms judged by the computing gate ──────────────────────────────
RUN_EXCERPT = "tests_total=120\ntests_failed=3\nversion=2.11.0\ncoverage=0.82"
for i in range(N):
    ch = _chain(f"https://example.org/run/{i}", RUN_EXCERPT)
    run("F. производное число (120-3)", "valid",
        f"117 tests passed [web:https://example.org/run/{i}]", ch)
    # 117 (120-3) is the right answer, so it must never serve as an invalid twin.
    wrong = 100 + i + (1 if 100 + i == 117 else 0)
    run("F. производное число (120-3)", "invalid",
        f"{wrong} tests passed [web:https://example.org/run/{i}]", ch)
    run("G. сравнение версии с границей", "valid",
        f"The report records a version below 3.0 [web:https://example.org/run/{i}]", ch)
    run("G. сравнение версии с границей", "invalid",
        f"The report records a version above 3.0 [web:https://example.org/run/{i}]", ch)

# ── H: off topic, yet sharing exactly one content word with the evidence ──
# The seventh gate demotes only at zero shared content words; H measures what that misses.
H_PAIRS = [
    ("Средняя задержка обработки запроса составила 20 миллисекунд.",
     "Средняя задержка обработки запроса — 20 миллисекунд.",
     "Скорость обработки заявок в отделении банка заметно выросла."),
    ("Выручка за квартал составила 20 миллионов рублей.",
     "Выручка за квартал — 20 миллионов рублей.",
     "Квартал застроен новыми жилыми домами."),
    ("В проекте участвуют 13 инженеров.",
     "В проекте участвуют 13 инженеров.",
     "В проекте памятника нашли ошибку планировки."),
    ("Хранилище содержит 41 запись о поставках.",
     "Хранилище содержит 41 запись о поставках.",
     "Запись концерта выложили на следующий день."),
]

for i in range(N):
    excerpt, valid_claim, offtopic_claim = H_PAIRS[i % len(H_PAIRS)]
    url = f"https://example.org/h/{i}"
    ch = _chain(url, excerpt)
    run("H. одно случайное общее слово", "valid", f"{valid_claim} [web:{url}]", ch)
    run("H. одно случайное общее слово", "invalid", f"{offtopic_claim} [web:{url}]", ch)

print("=== ДИСКРИМИНАЦИЯ ВЕРИФИКАТОРА (структурный проход, без LLM) ===")
print(f"    {N} пар на класс, валидный = дословный пересказ источника\n")
print(f"{'класс':<40}{'принят валидный':>16}{'принят НЕвалидный':>19}{'J':>8}")
for cls in sorted(TAB):
    v_ok, v_n = TAB[cls]["valid"]
    i_ok, i_n = TAB[cls]["invalid"]
    tpr, fpr = v_ok / v_n, i_ok / i_n
    print(f"{cls:<40}{tpr:>15.0%}{fpr:>18.0%}{tpr - fpr:>+8.2f}")
