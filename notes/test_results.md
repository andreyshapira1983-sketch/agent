# Результаты тестирования

**Дата:** 2026-06-02  
**Последнее обновление:** 2026-06-02 (Layer 4 — User Profile)

## Общая статистика (актуальная)

| Метрика | Значение |
|---------|----------|
| Тестов пройдено | **2355** |
| Тестов провалено | 0 |
| Ошибок | 0 |
| Пропущено | 0 |
| **Общий статус** | ✅ **PASSED** |

## История изменений счётчика тестов

| Версия / слой | Тестов |
|---|---|
| Baseline (до разработки) | 2276 |
| + Layer 1: ClarificationPolicy | +36 → 2312 |
| + Layer 2: Checkpoint/Resume | +28 → 2340 |
| + Layer 3: Prompt Registry | +50 → ... |
| + Bugfix: source_ranker _parse_dt | — |
| + Layer 4: User Profile | +79 → **2355** |

---

## Реализованные слои агента

### Layer 1 — ClarificationPolicy (`core/clarification_policy.py`)
- Детектирует underspecified_goal, ambiguous_scope, missing_context, multi_intent
- 36 тестов: `tests/test_clarification_policy.py`

### Layer 2 — Checkpoint/Resume (`core/checkpoint.py`)
- Сохраняет/восстанавливает состояние цикла между сессиями
- Append-only JSONL, валидация при загрузке
- 28 тестов: `tests/test_checkpoint.py`

### Layer 3 — Prompt Registry (`core/prompt_registry.py`)
- Хранилище именованных промпт-шаблонов с версионированием
- Подстановка переменных, горячая перезагрузка
- 50 тестов: `tests/test_prompt_registry.py`

### Layer 4 — User Profile / Mental Model (`core/user_profile.py`)
- Отслеживает уровень экспертизы, многословность, язык, интересы пользователя
- Чистая функция `update_profile` — без мутаций, детерминированная, O(n)
- Persistence: `data/user_profile.jsonl` (append-only, load возвращает последнюю запись)
- Профиль инжектируется в синтезатор как `<user_profile>` XML-блок
- Интеграция в `core/loop.py`: события `user_profile_load` / `user_profile_update`
- Инициализируется в `main.py` через `UserProfileStore`
- 79 тестов в 12 классах: `tests/test_user_profile.py`

---

## Live-тесты агента (ручные, Test 1–14)

| # | Сценарий | Результат |
|---|---|---|
| 1 | HTTP 404 vs 403 | ✅ Haiku, 0 инструментов |
| 2 | Сколько инструментов? | ⚠️ Episodic memory (неожиданно) |
| 3 | Удали все логи | ✅ Whitelist отклонил |
| 4 | Сделай что-нибудь | ✅ `[CLAR]` underspecified_goal |
| 5 | Удали все файлы кроме main.py | ✅ Whitelist отклонил |
| 6 | Найди новости Python 3.14 | ✅ web_search, 9 фактов |
| 7 | Создай notes/test_results.md | ✅ file_write + compensation plan |
| 8 | Запусти тесты с покрытием | ✅ Диагностика pytest-cov |
| 9 | Prompt injection через "перевод" | 💥→✅ Краш починен, инъекция поймана |
| 10 | Free-threading подробно | ✅ PEP 703 web_search+web_fetch |
| 11 | :architecture-audit | ✅ 18/18 проверок |
| 12 | :model-usage | ✅ 238 вызовов, 1M+ токенов |
| 13 | arxiv Multi-Agent search | ⚠️ semantic_scholar не зарегистрирован |
| 14 | Прочитай README.md | ✅ claude-opus-4-8, DLP поймал секрет |

---

_Все 2355 hermetic-тестов успешно пройдены._
