#!/usr/bin/env python3
"""MIR-060: does a resolved citation decide the verdict, whatever the claim says?

ЧТО БЫЛО НЕ ТАК С ПРЕДЫДУЩЕЙ ВЕРСИЕЙ. Она была написана против выдуманного API
(`Verifier`, `EvidenceRegistry`, `Claim`, `verify_claim`) — ничего этого в коде
нет, и запуск падал на первом импорте. Замысел шести случаев верный, поэтому он
сохранён; исполнение переписано под настоящий контракт `core.verifier.verify`.

ЧТО ЭТО МЕРЯЕТ. Для каждого случая известна ИСТИНА (поддерживает ли улика
утверждение), и отдельно — вердикт верификатора. Дефект воспроизведён там, где
ссылка разрешилась, истина «нет», а вердикт «verified».

БЕЗОПАСНОСТЬ. Чистое чтение: собственная цепочка улик в памяти, ни одного
обращения к рабочим хранилищам, ни одной записи в обучение. Временные файлы
живут в каталоге эксперимента и удаляются в конце.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _chain(*evidences) -> ProvenanceChain:
    chain = ProvenanceChain()
    for ev in evidences:
        chain.add(ev)
    return chain


def _answer(claim: str, citation: str) -> str:
    """Ответ в форме контракта вывода — иначе разбор не найдёт утверждений."""
    return (
        f"Conclusion: {claim} [{citation}]\n"
        f"Facts:\n- {claim} [{citation}]\n"
        f"Sources:\n1. {citation} - источник\n"
        "Confidence: high\nUnverified: nothing\n"
    )


def run_case(num, description, claim, excerpt, source_id, kind, truth, question):
    ev = make_evidence(
        kind=kind, source_id=source_id, obtained_via="experiment",
        claim="", excerpt=excerpt, confidence=0.9,
    )
    report = verify(
        answer=_answer(claim, f"{kind}:{source_id}"),
        chain=_chain(ev), user_question=question,
    )
    verdicts = [c.verdict for c in report.chunks if c.verdict != "structural"]
    accepted = "verified" in verdicts
    resolved = report.cited_but_unmatched_chunks == 0 and bool(verdicts)
    reproduced = bool(resolved and not truth and accepted)

    print(f"\n{'=' * 78}\nCASE {num}: {description}\n{'=' * 78}")
    print(f"  улика говорит   : {excerpt.strip()[:70]!r}")
    print(f"  утверждение     : {claim!r}")
    print(f"  ИСТИНА          : {'поддержано' if truth else 'НЕ поддержано'}")
    print(f"  ссылка разрешена: {resolved}")
    print(f"  вердикты        : {verdicts}")
    print(f"  принято         : {accepted}")
    print(f"  MIR-060 здесь   : {'ДА' if reproduced else 'нет'}")
    return {
        "case": num, "description": description, "claim": claim,
        "ground_truth_supported": truth, "citation_resolved": resolved,
        "verdicts": verdicts, "verifier_accepted": accepted,
        "mir060_reproduced": reproduced,
    }


def main() -> int:
    here = Path(__file__).parent
    artifact = here / "mir060_artifact.txt"
    artifact.write_text("Test artifact.\nValue: EXPERIMENT_VALUE_42\n", encoding="utf-8")
    body = artifact.read_text(encoding="utf-8")

    q = "какое значение в тестовом артефакте"
    results = [
        run_case(1, "ИСТИННЫЙ КОНТРОЛЬ — утверждение совпадает с уликой",
                 "Артефакт содержит значение EXPERIMENT_VALUE_42.",
                 body, "experiments/mir060_artifact.txt", "file", True, q),
        run_case(2, "ЛОЖНОЕ ЗНАЧЕНИЕ — ссылка та, значение чужое",
                 "Артефакт содержит значение WRONG_VALUE_99.",
                 body, "experiments/mir060_artifact.txt", "file", False, q),
        run_case(3, "ЧУЖОЙ РЕФЕРЕНТ — улика про A, утверждение про B",
                 "Артефакт B содержит значение VALUE_A.",
                 "Артефакт A\nЗначение: VALUE_A\n",
                 "experiments/mir060_a.txt", "file", False,
                 "что в артефакте B"),
        run_case(4, "ПРОТУХШЕЕ СОСТОЯНИЕ — улика старая, утверждение про текущее",
                 "Текущее состояние — STATE_ALPHA.",
                 "Состояние: STATE_ALPHA\n",
                 "experiments/mir060_state.txt", "file", False,
                 "каково состояние сейчас"),
        run_case(5, "УСИЛЕНИЕ — улика «настроен», утверждение «работает»",
                 "Компонент C работает и обрабатывает запросы.",
                 "Component C\nStatus: configured\nEnabled: true\n",
                 "config/component_c", "file", False,
                 "работает ли компонент C"),
        run_case(6, "ОТРИЦАНИЕ — улика говорит P, утверждение говорит НЕ P",
                 "Функция X выключена.",
                 "Feature X is enabled.\nStatus: active\n",
                 "config/feature_x", "file", False,
                 "включена ли функция X"),
    ]

    print(f"\n{'=' * 78}\nМАТРИЦА\n{'=' * 78}")
    for r in results:
        mark = "ДЕФЕКТ" if r["mir060_reproduced"] else "  ок  "
        print(f"  [{mark}] случай {r['case']}: истина="
              f"{r['ground_truth_supported']!s:5} принято={r['verifier_accepted']}")

    hit = [r["case"] for r in results if r["mir060_reproduced"]]
    print(f"\n{'=' * 78}")
    print("ВЕРДИКТ: ВОСПРОИЗВЕДЕНО в случаях " + str(hit) if hit
          else "ВЕРДИКТ: НЕ ВОСПРОИЗВЕДЕНО")
    print("=" * 78)

    (here / "mir060_results.json").write_text(
        json.dumps({"verdict": "REPRODUCED" if hit else "NOT_REPRODUCED",
                    "reproduced_cases": hit, "cases": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    for tmp in (artifact,):
        if tmp.exists():
            os.remove(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
