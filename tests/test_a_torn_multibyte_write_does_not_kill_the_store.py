"""Порванный посреди символа UTF-8 хвост не убивает хранилище состояния.

ВНЕШНЕЕ УТВЕРЖДЕНИЕ. Поле называет этот класс прямо: убийство процесса во
время дописывания в append-only журнал оставляет оборванную последнюю строку,
и восстановление на стороне ЧТЕНИЯ обязано ловить порчу построчно и читать
остальное дальше (langgraph #8039 про порядок записи и восстановление после
падения; claude-code #28809 — тот же класс на конфиге; несколько раннеров
агентов документируют «torn tail» как отдельный механизм порчи).

ПРИМЕНИМОСТЬ ЗДЕСЬ. У нас 24 файла состояния на одном читателе, и содержимое
русское по прямому правилу оператора: цели, вопросы, итоги, уроки. В
`episodic_memory.jsonl` 48 % байт не-ASCII. Значит обрыв записи попадает
ВНУТРЬ многобайтового символа с вероятностью около 9 % на строку — и это не
дисковая ошибка, а обычное убийство процесса.

ЗАМЕР ДО ПОЧИНКИ. 5 обрывов из 6, попавших внутрь символа, делали хранилище
нечитаемым НАВСЕГДА: `UnicodeDecodeError` летел из `p.read_text()` ещё до
любой построчной обработки, поэтому карантин — машинерия, заведённая ровно
для битых строк, — не запускался ни разу. Ни одного целого ряда не спасалось.

ПОЧЕМУ ЛОССИ-ДЕКОД ЗДЕСЬ БЫЛ БЫ НЕВЕРЕН. Строку, потребовавшую замены байта,
нельзя принимать: у не-конвертных (старых) рядов нет контрольной суммы, и
подменённый символ прошёл бы как значение. Поэтому строка, которая не
декодируется строго, отправляется в карантин целиком — как любая другая
непрочитанная строка.
"""
from __future__ import annotations

from pathlib import Path

from core.state_integrity import (
    append_state_jsonl,
    encode_state_row,
    quarantine_dir_for,
    read_state_jsonl,
)


def _cut_inside_a_character(row: str) -> int:
    """Смещение обрыва, попадающее внутрь многобайтового символа."""
    data = row.encode("utf-8")
    return next(i for i in range(1, len(data)) if (data[i] & 0xC0) == 0x80)


def test_a_kill_inside_a_cyrillic_character_keeps_the_other_rows(tmp_path: Path) -> None:
    path = tmp_path / "runtime_tasks.jsonl"
    append_state_jsonl(path, [{"id": i, "цель": f"задача {i}"} for i in range(4)])

    torn = encode_state_row({"goal": "разобраться почему self-build падает"})
    with path.open("ab") as fh:  # процесс убит ровно здесь
        fh.write(torn.encode("utf-8")[: _cut_inside_a_character(torn)])

    rows = read_state_jsonl(path)

    assert [r["id"] for r in rows] == [0, 1, 2, 3], (
        "обрыв внутри символа убил ВСЕ ряды: восстановление на чтении не "
        "построчное, и хранилище нечитаемо при каждом следующем чтении"
    )


def test_the_torn_line_is_quarantined_not_silently_dropped(tmp_path: Path) -> None:
    path = tmp_path / "episodic_memory.jsonl"
    append_state_jsonl(path, [{"id": 0, "итог": "успех"}])
    torn = encode_state_row({"summary": "проверка гипотезы про кириллицу"})
    with path.open("ab") as fh:
        fh.write(torn.encode("utf-8")[: _cut_inside_a_character(torn)])

    read_state_jsonl(path)

    quarantined = list(quarantine_dir_for(path).glob("*.bad.jsonl"))
    assert quarantined, "битая строка исчезла молча — потерю нельзя расследовать"
    assert "utf-8" in quarantined[0].read_text(encoding="utf-8"), (
        "причина в карантине не названа: запись должна отличать оборванный "
        "байт от битого json и от несошедшейся контрольной суммы"
    )


def test_a_disk_level_bad_byte_does_not_kill_the_store(tmp_path: Path) -> None:
    """Второй источник того же класса: ошибка носителя, а не обрыв записи."""
    path = tmp_path / "self_improvement_issues.jsonl"
    append_state_jsonl(path, [{"id": i} for i in range(6)])
    raw = bytearray(path.read_bytes())
    raw[200] = 0xFF
    path.write_bytes(bytes(raw))

    rows = read_state_jsonl(path)

    assert len(rows) == 5, f"выжило {len(rows)} из 6 — один байт унёс весь файл"


def test_a_healthy_file_is_read_byte_for_byte_as_before(tmp_path: Path) -> None:
    """Граница, которую починка не имеет права перейти: здоровый файл читается
    ровно как раньше, и ни один ряд не переписывается «на всякий случай»."""
    path = tmp_path / "clean.jsonl"
    payloads = [{"id": i, "текст": f"строка {i} с кириллицей"} for i in range(20)]
    append_state_jsonl(path, payloads)
    before = path.read_bytes()

    assert read_state_jsonl(path) == payloads
    assert path.read_bytes() == before, "здоровый файл переписан на чтении"
    assert not quarantine_dir_for(path).exists()
