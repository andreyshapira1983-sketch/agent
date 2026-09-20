"""Перечисленное в скобках — витрина, а не предмет отрицания.

Реконструкция живых эпизодов 2026-09-20: цепочки улик восстановлены из трасс
(tool_call + tool_result) и пересужены настоящим верификатором. Два
опровержения из шести оказались одного рода — `absence_refuted_by_evidence`,
где предметом отсутствия стали имена, которые фраза сама называет
НАЛИЧНЫМИ:

    «Каталог J4 содержит полный набор файлов версии (m11_partJ4_ex.md,
     m11_partJ4_notes.md и др.), но сам diff-файл не содержит формулировок
     теорем»  → предметы: m11_partj4_ex, m11_partj4_notes

    «В stdout probe выведена только строка заголовка
     «N,mean_ops_per_remove,max_ops», сами данные измерений отсутствуют»
     → предметы: max_ops, mean_ops_per_remove

Улика, разумеется, содержала эти имена — и верное утверждение объявлялось
ложью. Цена: `content_refuted` закрывает эпизоду вход в опыт раньше всех
прочих осей, и за четверо суток этот сигнал несли 105 из 142 недопущенных
эпизодов.

Разбор по частям здесь не спасал: запятая внутри скобок рвёт перечень
пополам, и вторая половина остаётся частью без глагола наличия. Лечится не
новым словом в словаре наличия, а различением показа и утверждения:
скобочный перечень и спан в кавычках — материал, который фраза
предъявляет. Обратные апострофы не трогаются: ими называют сам предмет.
"""
from __future__ import annotations

from core.verifier_absence import absence_refuted_by_excerpt, absence_subjects

_LISTED = ("- Каталог J4 содержит полный набор файлов версии "
           "(m11_partJ4_ex.md, m11_partJ4_notes.md и др.), но сам diff-файл "
           "не содержит формулировок теорем")
_QUOTED = ("- В stdout probe выведена только строка заголовка "
           "«N,mean_ops_per_remove,max_ops», сами данные измерений отсутствуют")
_NAMED = ("Однако в твоей среде выполнения этот атрибут отсутствует, поэтому "
          "код с `itertools.batched` здесь работать не будет.")


def test_a_parenthesised_listing_is_not_the_subject_of_the_denial() -> None:
    assert absence_subjects(_LISTED) == set()
    listing = "m11_partJ4_ex.md\nm11_partJ4_notes.md\nm11_partJ4.md\n"
    assert absence_refuted_by_excerpt(_LISTED, listing) is False


def test_a_quoted_header_is_not_the_subject_either() -> None:
    assert absence_subjects(_QUOTED) == set()
    stdout = "N,mean_ops_per_remove,max_ops\n"
    assert absence_refuted_by_excerpt(_QUOTED, stdout) is False


def test_a_name_the_claim_speaks_is_still_its_subject() -> None:
    assert absence_subjects(_NAMED) == {"itertools.batched"}
    docs = "itertools.batched(iterable, n, strict=False)\nAdded in version 3.12."
    assert absence_refuted_by_excerpt(_NAMED, docs) is True
