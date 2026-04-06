"""Тест LLM Error Memory в PersistentBrain."""
import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.persistent_brain import PersistentBrain


def test_llm_error_memory():
    td = tempfile.mkdtemp()
    try:
        brain = PersistentBrain(data_dir=td)

        # 1. Записываем ошибки LLM
        brain.record_llm_error(
            task='Напиши тест для calculator.py',
            error_type='validation_fail',
            what_went_wrong='План не содержит исполняемых действий — LLM написал прозу',
            correct_approach='Нужно использовать формат WRITE: filename + CONTENT: код',
            category='PLANNING',
        )
        brain.record_llm_error(
            task='Проанализируй ошибку в модуле',
            error_type='hallucination',
            what_went_wrong='LLM галлюцинировал несуществующий файл utils/helper.py',
            category='DIAGNOSIS',
        )
        # Дубликат — должен увеличить repeat_count, а не добавить новую запись
        brain.record_llm_error(
            task='Другая задача с прозой',
            error_type='validation_fail',
            what_went_wrong='План не содержит исполняемых действий — LLM написал прозу',
        )

        assert len(brain._llm_errors) == 2, f"Expected 2, got {len(brain._llm_errors)}"
        assert brain._llm_errors[0]['repeat_count'] == 2, "Dedup should increment repeat_count"

        # 2. Получаем hints
        hints = brain.get_llm_error_hints(task='написать тест', limit=5)
        print('=== HINTS ===')
        for h in hints:
            print(h)
        assert len(hints) == 2, f"Expected 2 hints, got {len(hints)}"
        assert 'НЕ ДЕЛАЙ' in hints[0], "First hint should have correction"
        assert 'ИЗБЕГАЙ' in hints[1], "Second hint (no correction) should say ИЗБЕГАЙ"

        # 3. Статистика
        stats = brain.get_llm_error_stats()
        print(f"\nTotal: {stats['total']}, Active: {stats['active']}")
        print(f"By type: {stats['by_type']}")
        assert stats['total'] == 2
        assert stats['active'] == 2
        assert stats['by_type']['validation_fail'] == 1
        assert stats['by_type']['hallucination'] == 1

        # 4. mark_resolved
        brain.mark_llm_error_resolved('llm галлюцинировал')
        hints2 = brain.get_llm_error_hints(limit=5)
        assert len(hints2) == 1, "After resolving one, should have 1 hint"

        # 5. Save/Load
        brain._save_llm_errors()
        brain2 = PersistentBrain(data_dir=td)
        brain2._load_llm_errors()
        assert len(brain2._llm_errors) == 2, "Persisted errors should load"
        hints3 = brain2.get_llm_error_hints(limit=5)
        assert len(hints3) == 1, "Resolved flag should persist"

        print("\n✅ ALL TESTS PASSED")
    finally:
        shutil.rmtree(td)


if __name__ == '__main__':
    test_llm_error_memory()
