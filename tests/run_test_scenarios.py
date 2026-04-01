"""
run_test_scenarios.py — Прогон тестовых сценариев через автономный агент.

Читает файл "Текстовый документ .json" (каждый абзац = сценарий),
запускает каждый как цель агента и сохраняет результаты в outputs/test_results/.

Запуск:
    python run_test_scenarios.py                    # все 50 сценариев, 2 цикла каждый
    python run_test_scenarios.py --cycles 3         # 3 цикла на сценарий
    python run_test_scenarios.py --start 1 --end 5  # только сценарии 1–5
    python run_test_scenarios.py --list             # только напечатать список сценариев
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs", "test_results")
SCENARIOS_FILE = os.path.join(BASE_DIR, "Текстовый документ .json")


# ─────────────────────────────────────────────────────────────────────────────
# Загрузка сценариев
# ─────────────────────────────────────────────────────────────────────────────

def load_scenarios() -> list:
    """Читает файл и возвращает список строк-сценариев (один абзац = один сценарий)."""
    with open(SCENARIOS_FILE, encoding="utf-8") as f:
        text = f.read()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs


# ─────────────────────────────────────────────────────────────────────────────
# Основной прогон
# ─────────────────────────────────────────────────────────────────────────────

def run_scenarios(cycles_per_scenario: int = 2, start_idx: int = 1, end_idx=None):
    """Запускает выбранные сценарии через полный стек агента."""

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    scenarios = load_scenarios()
    total     = len(scenarios)

    if end_idx is None:
        end_idx = total
    end_idx   = min(end_idx, total)
    to_run    = scenarios[start_idx - 1 : end_idx]

    print(f"Сценариев в файле: {total}")
    print(f"Запускаю: {start_idx}–{end_idx}  ({len(to_run)} шт.)")
    print(f"Циклов на сценарий: {cycles_per_scenario}")
    print(f"Результаты → {OUTPUT_DIR}")
    print("=" * 70)

    # ── Инициализация агента ──────────────────────────────────────────────────
    print("Инициализация агента (все 46 слоёв)...")

    # Подавляем автоматическое открытие браузера во время init
    import webbrowser as _wb
    _orig_open = _wb.open
    _wb.open   = lambda *a, **kw: None

    sys.path.insert(0, BASE_DIR)
    from agent import build_agent
    components  = build_agent()
    loop        = components["loop"]
    monitoring  = components["monitoring"]

    _wb.open = _orig_open  # восстанавливаем

    print("Агент готов. Начинаю тесты.\n")

    # ── Итоговый отчёт ────────────────────────────────────────────────────────
    summary = {
        "started_at": datetime.now().isoformat(),
        "total_scenarios": len(to_run),
        "cycles_per_scenario": cycles_per_scenario,
        "results": [],
    }

    for offset, scenario_text in enumerate(to_run):
        scenario_num   = start_idx + offset
        scenario_label = f"scenario_{scenario_num:02d}"

        # Короткий заголовок для консоли (первое слово-тема + начало)
        first_period = scenario_text.find(".")
        header = scenario_text[:first_period].strip() if first_period > 0 else scenario_text[:60]

        print(f"[{scenario_num}/{end_idx}] {header}")

        entry = {
            "index":      scenario_num,
            "label":      scenario_label,
            "scenario":   scenario_text,
            "cycles":     [],
            "status":     "pending",
            "started_at": datetime.now().isoformat(),
        }

        try:
            loop.set_goal(scenario_text)

            for c_num in range(cycles_per_scenario):
                cycle      = loop.step()
                cycle_dict = cycle.to_dict() if hasattr(cycle, "to_dict") else {"raw": str(cycle)}

                # Убираем из cycle_dict огромные поля, чтобы файлы были читаемыми
                for big_field in ("observation",):
                    obs = cycle_dict.get(big_field)
                    if isinstance(obs, dict):
                        # Обрезаем текстовые значения > 500 символов
                        for k, v in obs.items():
                            if isinstance(v, str) and len(v) > 500:
                                obs[k] = v[:500] + "…[обрезано]"

                entry["cycles"].append(cycle_dict)

                ok = "✓" if cycle_dict.get("success") else "○"
                errors_str = ""
                if cycle_dict.get("errors"):
                    errors_str = "  (!)" + "; ".join(str(e) for e in cycle_dict["errors"])[:120]
                print(f"    цикл {c_num + 1}/{cycles_per_scenario} {ok}{errors_str}")

            entry["status"] = "completed"

        except KeyboardInterrupt:
            print(f"\n[Прервано на сценарии {scenario_num}]")
            entry["status"] = "interrupted"
            summary["results"].append(entry)
            break

        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            entry["status"] = "error"
            entry["error"]  = str(exc)
            print(f"    ОШИБКА: {exc}")
            monitoring.warning(f"[test_runner] сценарий {scenario_num} упал: {exc}", source="test_runner")

        entry["finished_at"] = datetime.now().isoformat()
        summary["results"].append(entry)

        # Сохраняем каждый сценарий в отдельный файл
        out_path = os.path.join(OUTPUT_DIR, f"{scenario_label}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, default=str)

        print(f"    → outputs/test_results/{scenario_label}.json\n")

        # Небольшая пауза между сценариями
        time.sleep(1.5)

    # ── Итоговый отчёт ────────────────────────────────────────────────────────
    summary["finished_at"] = datetime.now().isoformat()
    summary_path = os.path.join(OUTPUT_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    completed   = sum(1 for r in summary["results"] if r["status"] == "completed")
    errors      = sum(1 for r in summary["results"] if r["status"] == "error")
    interrupted = sum(1 for r in summary["results"] if r["status"] == "interrupted")
    ran         = len(summary["results"])

    print("=" * 70)
    print(f"Итого: {completed} завершено, {errors} ошибок, {interrupted} прервано  (из {ran})")
    print("Сводный отчёт: outputs/test_results/summary.json")

    # ── Короткий список уроков в долгосрочную память ─────────────────────────
    knowledge = components.get("knowledge")
    if knowledge and completed > 0:
        lessons_key   = "test_run:lessons:" + datetime.now().strftime("%Y%m%d_%H%M")
        lessons_value = (
            f"Прогон {ran} тестовых сценариев завершён {datetime.now().isoformat()}. "
            f"Успешно: {completed}, ошибок: {errors}. "
            f"Результаты в outputs/test_results/."
        )
        try:
            knowledge.store_long_term(key=lessons_key, value=lessons_value)
            print(f"Урок записан в долгосрочную память: {lessons_key}")
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
            pass


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Прогон тестовых сценариев через автономный агент"
    )
    parser.add_argument(
        "--cycles", type=int, default=2,
        help="Число циклов autonomous loop на каждый сценарий (по умолчанию: 2)"
    )
    parser.add_argument(
        "--start", type=int, default=1,
        help="Первый сценарий (1-based, по умолчанию: 1)"
    )
    parser.add_argument(
        "--end", type=int, default=None,
        help="Последний сценарий включительно (по умолчанию: все)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Только вывести список сценариев без запуска"
    )
    args = parser.parse_args()

    if args.list:
        scenarios = load_scenarios()
        for i, s in enumerate(scenarios, 1):
            first_period = s.find(".")
            header = s[:first_period].strip() if first_period > 0 else s[:70]
            print(f"  {i:2d}. {header}")
        print(f"\nВсего: {len(scenarios)} сценариев")
        return

    run_scenarios(
        cycles_per_scenario=args.cycles,
        start_idx=args.start,
        end_idx=args.end,
    )


if __name__ == "__main__":
    main()
